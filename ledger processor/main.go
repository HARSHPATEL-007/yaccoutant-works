package main

import (
	"context"
	"database/sql"
	"fmt"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/google/uuid"
	_ "github.com/lib/pq"
	"github.com/redis/go-redis/v9"
	"github.com/shopspring/decimal"
	"go.uber.org/zap"
)

// Config holds service configuration
type Config struct {
	DBDSN       string
	RedisAddr   string
	RedisPass   string
	Port        string
	Environment string
}

// LedgerEntry represents a double-entry bookkeeping transaction
type LedgerEntry struct {
	ID                   uuid.UUID       `json:"id" db:"id"`
	ClientID             uuid.UUID       `json:"client_id" db:"client_id" binding:"required"`
	AccountCode          string          `json:"account_code" db:"account_code" binding:"required"`
	TransactionDate      string          `json:"transaction_date" db:"transaction_date" binding:"required"`
	Debit                decimal.Decimal `json:"debit" db:"debit"`
	Credit               decimal.Decimal `json:"credit" db:"credit"`
	Description          string          `json:"description" db:"description"`
	GSTIN                *string         `json:"gstin,omitempty" db:"gstin"`
	HSNCode              *string         `json:"hsn_code,omitempty" db:"hsn_code"`
	ReconciliationStatus string          `json:"reconciliation_status" db:"reconciliation_status"`
	DocumentID           *uuid.UUID      `json:"document_id,omitempty" db:"document_id"`
	PostedBy             *uuid.UUID      `json:"posted_by,omitempty" db:"posted_by"`
	CreatedAt            time.Time       `json:"created_at" db:"created_at"`
}

// DoubleEntryPair ensures debit equals credit for a transaction
type DoubleEntryPair struct {
	Entries []LedgerEntry `json:"entries" binding:"required,min=2"`
}

// AppContext holds shared dependencies
type AppContext struct {
	DB     *sql.DB
	Redis  *redis.Client
	Logger *zap.Logger
	Config *Config
}

func main() {
	logger, _ := zap.NewProduction()
	defer logger.Sync()

	cfg := &Config{
		DBDSN:       getEnv("DB_DSN", "postgres://localhost:5432/accounting_platform?sslmode=disable"),
		RedisAddr:   getEnv("REDIS_ADDR", "localhost:6379"),
		RedisPass:   getEnv("REDIS_PASSWORD", ""),
		Port:        getEnv("PORT", "8080"),
		Environment: getEnv("ENV", "development"),
	}

	db, err := sql.Open("postgres", cfg.DBDSN)
	if err != nil {
		logger.Fatal("failed to open database", zap.Error(err))
	}
	defer db.Close()

	db.SetMaxOpenConns(25)
	db.SetMaxIdleConns(10)
	db.SetConnMaxLifetime(5 * time.Minute)

	if err := db.Ping(); err != nil {
		logger.Fatal("failed to ping database", zap.Error(err))
	}

	rdb := redis.NewClient(&redis.Options{
		Addr:     cfg.RedisAddr,
		Password: cfg.RedisPass,
		DB:       0,
		PoolSize: 20,
	})

	app := &AppContext{
		DB:     db,
		Redis:  rdb,
		Logger: logger,
		Config: cfg,
	}

	if cfg.Environment == "production" {
		gin.SetMode(gin.ReleaseMode)
	}

	router := gin.New()
	router.Use(gin.Recovery())
	router.Use(correlationIDMiddleware())
	router.Use(loggingMiddleware(logger))

	v1 := router.Group("/api/v1/ledger")
	{
		v1.POST("/entries", app.createEntry)
		v1.POST("/batch", app.createBatchEntries)
		v1.GET("/entries/:client_id", app.listEntries)
		v1.GET("/reconciliation/:client_id", app.getReconciliationStatus)
		v1.POST("/reconciliation/match", app.matchEntries)
	}

	router.GET("/health", app.healthCheck)

	logger.Info("ledger-processor starting", zap.String("port", cfg.Port))
	if err := router.Run(":" + cfg.Port); err != nil {
		logger.Fatal("server failed", zap.Error(err))
	}
}

func (app *AppContext) createEntry(c *gin.Context) {
	var entry LedgerEntry
	if err := c.ShouldBindJSON(&entry); err != nil {
		app.respondError(c, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}

	// Validate double-entry: either debit or credit must be > 0, not both
	if entry.Debit.IsPositive() && entry.Credit.IsPositive() {
		app.respondError(c, http.StatusBadRequest, "DOUBLE_ENTRY_VIOLATION", 
			"An entry cannot have both debit and credit > 0")
		return
	}
	if !entry.Debit.IsPositive() && !entry.Credit.IsPositive() {
		app.respondError(c, http.StatusBadRequest, "ZERO_ENTRY", 
			"Either debit or credit must be greater than 0")
		return
	}

	// Validate GSTIN format if provided (15 chars for India)
	if entry.GSTIN != nil && len(*entry.GSTIN) != 15 {
		app.respondError(c, http.StatusBadRequest, "INVALID_GSTIN", 
			"GSTIN must be 15 characters")
		return
	}

	// Set client ID from context (set by auth middleware)
	clientID, _ := uuid.Parse(c.GetString("client_id"))
	entry.ClientID = clientID

	tx, err := app.DB.BeginTx(c.Request.Context(), nil)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "TX_ERROR", err.Error())
		return
	}
	defer tx.Rollback()

	query := `
		INSERT INTO ledgers (client_id, account_code, transaction_date, debit, credit, 
			description, gstin, hsn_code, document_id, posted_by)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		RETURNING id, created_at
	`

	err = tx.QueryRowContext(c.Request.Context(), query,
		entry.ClientID, entry.AccountCode, entry.TransactionDate,
		entry.Debit, entry.Credit, entry.Description,
		entry.GSTIN, entry.HSNCode, entry.DocumentID, entry.PostedBy,
	).Scan(&entry.ID, &entry.CreatedAt)

	if err != nil {
		app.Logger.Error("insert failed", zap.Error(err), zap.String("correlation_id", c.GetString("correlation_id")))
		app.respondError(c, http.StatusInternalServerError, "INSERT_FAILED", "Failed to create ledger entry")
		return
	}

	// Invalidate reconciliation cache
	cacheKey := fmt.Sprintf("recon:%s:%s", entry.ClientID, entry.TransactionDate[:7])
	app.Redis.Del(c.Request.Context(), cacheKey)

	if err := tx.Commit(); err != nil {
		app.respondError(c, http.StatusInternalServerError, "COMMIT_FAILED", err.Error())
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"data": entry,
		"meta": gin.H{"correlation_id": c.GetString("correlation_id")},
	})
}

func (app *AppContext) createBatchEntries(c *gin.Context) {
	var pair DoubleEntryPair
	if err := c.ShouldBindJSON(&pair); err != nil {
		app.respondError(c, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}

	// Validate double-entry balance: sum(debits) == sum(credits)
	var totalDebit, totalCredit decimal.Decimal
	for _, e := range pair.Entries {
		totalDebit = totalDebit.Add(e.Debit)
		totalCredit = totalCredit.Add(e.Credit)
	}

	if !totalDebit.Equal(totalCredit) {
		app.respondError(c, http.StatusBadRequest, "UNBALANCED_ENTRY",
			fmt.Sprintf("Total debit (%s) must equal total credit (%s)", totalDebit, totalCredit))
		return
	}

	clientID, _ := uuid.Parse(c.GetString("client_id"))

	tx, err := app.DB.BeginTx(c.Request.Context(), nil)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "TX_ERROR", err.Error())
		return
	}
	defer tx.Rollback()

	stmt, err := tx.PrepareContext(c.Request.Context(), `
		INSERT INTO ledgers (client_id, account_code, transaction_date, debit, credit, 
			description, gstin, hsn_code, document_id, posted_by)
		VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
		RETURNING id, created_at
	`)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "PREPARE_FAILED", err.Error())
		return
	}
	defer stmt.Close()

	createdEntries := make([]LedgerEntry, 0, len(pair.Entries))
	for _, entry := range pair.Entries {
		entry.ClientID = clientID
		var id uuid.UUID
		var createdAt time.Time
		err := stmt.QueryRowContext(c.Request.Context(),
			entry.ClientID, entry.AccountCode, entry.TransactionDate,
			entry.Debit, entry.Credit, entry.Description,
			entry.GSTIN, entry.HSNCode, entry.DocumentID, entry.PostedBy,
		).Scan(&id, &createdAt)
		if err != nil {
			app.Logger.Error("batch insert failed", zap.Error(err))
			app.respondError(c, http.StatusInternalServerError, "BATCH_INSERT_FAILED", err.Error())
			return
		}
		entry.ID = id
		entry.CreatedAt = createdAt
		createdEntries = append(createdEntries, entry)
	}

	if err := tx.Commit(); err != nil {
		app.respondError(c, http.StatusInternalServerError, "COMMIT_FAILED", err.Error())
		return
	}

	c.JSON(http.StatusCreated, gin.H{
		"data": createdEntries,
		"count": len(createdEntries),
		"balanced": true,
		"total_amount": totalDebit.String(),
	})
}

func (app *AppContext) listEntries(c *gin.Context) {
	clientID := c.Param("client_id")
	if clientID == "" {
		app.respondError(c, http.StatusBadRequest, "MISSING_CLIENT_ID", "Client ID required")
		return
	}

	// Verify user has access to this client (simplified)
	if c.GetString("client_id") != clientID {
		app.respondError(c, http.StatusForbidden, "ACCESS_DENIED", "Cannot access this client's data")
		return
	}

	period := c.Query("period") // YYYY-MM format
	accountCode := c.Query("account_code")
	status := c.Query("status")

	query := `
		SELECT id, client_id, account_code, transaction_date, debit, credit, 
		       description, gstin, hsn_code, reconciliation_status, document_id, posted_by, created_at
		FROM ledgers 
		WHERE client_id = $1
	`
	args := []interface{}{clientID}
	argCount := 1

	if period != "" {
		argCount++
		query += fmt.Sprintf(" AND TO_CHAR(transaction_date, 'YYYY-MM') = $%d", argCount)
		args = append(args, period)
	}
	if accountCode != "" {
		argCount++
		query += fmt.Sprintf(" AND account_code = $%d", argCount)
		args = append(args, accountCode)
	}
	if status != "" {
		argCount++
		query += fmt.Sprintf(" AND reconciliation_status = $%d", argCount)
		args = append(args, status)
	}

	query += " ORDER BY transaction_date DESC, created_at DESC LIMIT 1000"

	rows, err := app.DB.QueryContext(c.Request.Context(), query, args...)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "QUERY_FAILED", err.Error())
		return
	}
	defer rows.Close()

	entries := []LedgerEntry{}
	for rows.Next() {
		var e LedgerEntry
		var debit, credit sql.NullFloat64
		err := rows.Scan(
			&e.ID, &e.ClientID, &e.AccountCode, &e.TransactionDate,
			&debit, &credit, &e.Description, &e.GSTIN, &e.HSNCode,
			&e.ReconciliationStatus, &e.DocumentID, &e.PostedBy, &e.CreatedAt,
		)
		if err != nil {
			continue
		}
		if debit.Valid {
			e.Debit = decimal.NewFromFloat(debit.Float64)
		}
		if credit.Valid {
			e.Credit = decimal.NewFromFloat(credit.Float64)
		}
		entries = append(entries, e)
	}

	c.JSON(http.StatusOK, gin.H{
		"data": entries,
		"count": len(entries),
	})
}

func (app *AppContext) getReconciliationStatus(c *gin.Context) {
	clientID := c.Param("client_id")
	period := c.Query("period") // YYYY-MM

	if period == "" {
		period = time.Now().Format("2006-01")
	}

	cacheKey := fmt.Sprintf("recon:%s:%s", clientID, period)
	cached, err := app.Redis.Get(c.Request.Context(), cacheKey).Result()
	if err == nil && cached != "" {
		c.JSON(http.StatusOK, gin.H{"data": cached, "cached": true})
		return
	}

	query := `
		SELECT 
			COUNT(*) as total,
			COUNT(*) FILTER (WHERE reconciliation_status = 'matched') as matched,
			COUNT(*) FILTER (WHERE reconciliation_status = 'mismatched') as mismatched,
			COUNT(*) FILTER (WHERE reconciliation_status = 'pending') as pending,
			SUM(debit) as total_debit,
			SUM(credit) as total_credit
		FROM ledgers
		WHERE client_id = $1 AND TO_CHAR(transaction_date, 'YYYY-MM') = $2
	`

	var stats struct {
		Total       int             `json:"total"`
		Matched     int             `json:"matched"`
		Mismatched  int             `json:"mismatched"`
		Pending     int             `json:"pending"`
		TotalDebit  decimal.Decimal `json:"total_debit"`
		TotalCredit decimal.Decimal `json:"total_credit"`
	}

	var td, tc sql.NullFloat64
	err = app.DB.QueryRowContext(c.Request.Context(), query, clientID, period).Scan(
		&stats.Total, &stats.Matched, &stats.Mismatched, &stats.Pending, &td, &tc,
	)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "QUERY_FAILED", err.Error())
		return
	}
	if td.Valid {
		stats.TotalDebit = decimal.NewFromFloat(td.Float64)
	}
	if tc.Valid {
		stats.TotalCredit = decimal.NewFromFloat(tc.Float64)
	}

	// Cache for 5 minutes
	app.Redis.Set(c.Request.Context(), cacheKey, stats, 5*time.Minute)

	c.JSON(http.StatusOK, gin.H{"data": stats, "period": period})
}

func (app *AppContext) matchEntries(c *gin.Context) {
	// Simplified reconciliation: match invoice entries with bank statement entries
	// In production, this uses fuzzy matching on amount + date + reference
	type MatchRequest struct {
		InvoiceEntryID    uuid.UUID `json:"invoice_entry_id" binding:"required"`
		BankEntryID       uuid.UUID `json:"bank_entry_id" binding:"required"`
		ConfidenceScore   float64   `json:"confidence_score" binding:"required,min=0,max=1"`
	}

	var req MatchRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		app.respondError(c, http.StatusBadRequest, "INVALID_REQUEST", err.Error())
		return
	}

	if req.ConfidenceScore < 0.85 {
		app.respondError(c, http.StatusBadRequest, "LOW_CONFIDENCE", 
			"Confidence score must be >= 0.85 for auto-matching")
		return
	}

	query := `
		UPDATE ledgers 
		SET reconciliation_status = 'matched', updated_at = NOW()
		WHERE id = $1 OR id = $2
	`
	_, err := app.DB.ExecContext(c.Request.Context(), query, req.InvoiceEntryID, req.BankEntryID)
	if err != nil {
		app.respondError(c, http.StatusInternalServerError, "UPDATE_FAILED", err.Error())
		return
	}

	c.JSON(http.StatusOK, gin.H{
		"status": "matched",
		"invoice_entry_id": req.InvoiceEntryID,
		"bank_entry_id": req.BankEntryID,
	})
}

func (app *AppContext) healthCheck(c *gin.Context) {
	ctx, cancel := context.WithTimeout(c.Request.Context(), 2*time.Second)
	defer cancel()

	dbErr := app.DB.PingContext(ctx)
	redisErr := app.Redis.Ping(ctx).Err()

	status := "healthy"
	if dbErr != nil || redisErr != nil {
		status = "degraded"
	}

	c.JSON(http.StatusOK, gin.H{
		"status":  status,
		"db":      dbErr == nil,
		"redis":   redisErr == nil,
		"version": "1.0.0",
	})
}

func (app *AppContext) respondError(c *gin.Context, code int, errCode, message string) {
	c.JSON(code, gin.H{
		"error": gin.H{
			"code":    errCode,
			"message": message,
		},
		"meta": gin.H{
			"correlation_id": c.GetString("correlation_id"),
		},
	})
}

func correlationIDMiddleware() gin.HandlerFunc {
	return func(c *gin.Context) {
		correlationID := c.GetHeader("X-Correlation-ID")
		if correlationID == "" {
			correlationID = uuid.New().String()
		}
		c.Set("correlation_id", correlationID)
		c.Header("X-Correlation-ID", correlationID)
		c.Next()
	}
}

func loggingMiddleware(logger *zap.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		path := c.Request.URL.Path
		raw := c.Request.URL.RawQuery

		c.Next()

		latency := time.Since(start)
		clientID := c.GetString("client_id")

		if raw != "" {
			path = path + "?" + raw
		}

		logger.Info("request",
			zap.String("method", c.Request.Method),
			zap.String("path", path),
			zap.Int("status", c.Writer.Status()),
			zap.Duration("latency", latency),
			zap.String("client_id", clientID),
			zap.String("correlation_id", c.GetString("correlation_id")),
			zap.String("ip", c.ClientIP()),
		)
	}
}

func getEnv(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}