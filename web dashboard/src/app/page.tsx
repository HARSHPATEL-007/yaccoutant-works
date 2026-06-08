import { ComplianceDashboard } from "@/components/compliance-dashboard";
import { AIChatWidget } from "@/components/ai-chat-widget";

export default function Home() {
  return (
    <main className="min-h-screen bg-gray-50">
      <div className="container mx-auto px-4 py-8">
        <header className="mb-8">
          <h1 className="text-3xl font-bold text-gray-900">Compliance Autopilot</h1>
          <p className="text-gray-600 mt-2">SME Foundation — GST, Ledgers, CMA Reports</p>
        </header>
        
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
          <div className="lg:col-span-2">
            <ComplianceDashboard />
          </div>
          <div className="lg:col-span-1">
            <AIChatWidget />
          </div>
        </div>
      </div>
    </main>
  );
}