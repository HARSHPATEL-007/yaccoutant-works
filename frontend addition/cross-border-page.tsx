"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Globe, FileText, CheckCircle, AlertTriangle, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";

interface FEMAStep {
  id: number;
  title: string;
  description: string;
  status: "completed" | "in_progress" | "pending";
  documents: string[];
}

interface DTAATreaty {
  country: string;
  article_number: string;
  article_title: string;
  plain_english: string;
  original_text: string;
}

export default function CrossBorderNavigator() {
  const [selectedCountry, setSelectedCountry] = useState("USA");
  const [activeTab, setActiveTab] = useState<"fema" | "dtaa" | "setup">("fema");

  const { data: femaSteps } = useQuery<FEMAStep[]>({
    queryKey: ["fema-timeline", selectedCountry],
    queryFn: async () => {
      const res = await api.get(`/foreign-entity/fema-timeline?country=${selectedCountry}`);
      return res.data.data;
    },
    placeholderData: [
      { id: 1, title: "FEMA Approval", description: "Obtain RBI approval for foreign investment under FEMA 20", status: "completed", documents: ["FC-GPR", "Board Resolution"] },
      { id: 2, title: "Entity Incorporation", description: "Incorporate Indian subsidiary (Pvt Ltd)", status: "in_progress", documents: ["SPICe+ Form", "MOA/AOA"] },
      { id: 3, title: "Tax Registration", description: "Apply for PAN and TAN", status: "pending", documents: ["PAN Application", "TAN Form 49B"] },
      { id: 4, title: "GST Registration", description: "Mandatory GST registration for B2B operations", status: "pending", documents: ["GST REG-01"] },
      { id: 5, title: "Bank Account", description: "Open current account with AD Category-I bank", status: "pending", documents: ["FEMA Declaration", "KYC Documents"] },
    ]
  });

  const { data: dtaaArticles } = useQuery<DTAATreaty[]>({
    queryKey: ["dtaa", selectedCountry],
    queryFn: async () => {
      const res = await api.get(`/rag/search?query=DTAA ${selectedCountry} article&doc_types=dtaa_treaty`);
      return res.data.results || [];
    },
  });

  return (
    <div className="min-h-screen bg-gray-50 p-8">
      <div className="max-w-6xl mx-auto">
        <header className="mb-8">
          <div className="flex items-center gap-3 mb-2">
            <Globe className="w-8 h-8 text-blue-600" />
            <h1 className="text-3xl font-bold text-gray-900">Cross-Border Navigator</h1>
          </div>
          <p className="text-gray-600">FEMA compliance, DTAA advisory, and subsidiary setup workflows</p>
        </header>

        <div className="bg-white rounded-lg shadow mb-6">
          <div className="flex border-b border-gray-200">
            {(["fema", "dtaa", "setup"] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setActiveTab(tab)}
                className={`px-6 py-4 font-medium text-sm capitalize ${
                  activeTab === tab
                    ? "border-b-2 border-blue-600 text-blue-600"
                    : "text-gray-500 hover:text-gray-700"
                }`}
              >
                {tab === "fema" ? "FEMA Timeline" : tab === "dtaa" ? "DTAA Articles" : "Subsidiary Setup"}
              </button>
            ))}
          </div>

          <div className="p-6">
            {activeTab === "fema" && (
              <div className="space-y-4">
                <div className="flex justify-between items-center mb-4">
                  <h2 className="text-lg font-semibold">Compliance Timeline</h2>
                  <select
                    value={selectedCountry}
                    onChange={(e) => setSelectedCountry(e.target.value)}
                    className="border border-gray-300 rounded-md px-3 py-1 text-sm"
                  >
                    <option value="USA">United States</option>
                    <option value="UK">United Kingdom</option>
                    <option value="Singapore">Singapore</option>
                    <option value="UAE">UAE</option>
                    <option value="Germany">Germany</option>
                  </select>
                </div>

                <div className="relative">
                  {femaSteps?.map((step, idx) => (
                    <div key={step.id} className="flex gap-4 mb-6">
                      <div className="flex flex-col items-center">
                        <div className={`w-8 h-8 rounded-full flex items-center justify-center ${
                          step.status === "completed" ? "bg-green-100 text-green-600" :
                          step.status === "in_progress" ? "bg-blue-100 text-blue-600" :
                          "bg-gray-100 text-gray-400"
                        }`}>
                          {step.status === "completed" ? <CheckCircle className="w-5 h-5" /> :
                           step.status === "in_progress" ? <AlertTriangle className="w-5 h-5" /> :
                           <span className="text-sm font-medium">{idx + 1}</span>}
                        </div>
                        {idx < (femaSteps?.length || 0) - 1 && (
                          <div className="w-0.5 h-full bg-gray-200 my-1" />
                        )}
                      </div>
                      <div className="flex-1 pb-6">
                        <h3 className="font-semibold text-gray-900">{step.title}</h3>
                        <p className="text-sm text-gray-600 mt-1">{step.description}</p>
                        <div className="flex flex-wrap gap-2 mt-2">
                          {step.documents.map((doc) => (
                            <span key={doc} className="inline-flex items-center gap-1 px-2 py-1 bg-gray-100 rounded text-xs text-gray-700">
                              <FileText className="w-3 h-3" />
                              {doc}
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {activeTab === "dtaa" && (
              <div className="space-y-4">
                <h2 className="text-lg font-semibold mb-4">DTAA Article Viewer</h2>
                <div className="grid gap-4">
                  {dtaaArticles && dtaaArticles.length > 0 ? dtaaArticles.map((article) => (
                    <div key={article.article_number} className="border border-gray-200 rounded-lg p-4">
                      <div className="flex items-center justify-between mb-2">
                        <h3 className="font-semibold text-blue-700">
                          Article {article.article_number}: {article.article_title}
                        </h3>
                        <span className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded">
                          {article.country}
                        </span>
                      </div>
                      <div className="grid md:grid-cols-2 gap-4 mt-3">
                        <div className="bg-gray-50 p-3 rounded text-sm">
                          <p className="font-medium text-gray-700 mb-1">Original Text</p>
                          <p className="text-gray-600 italic">{article.original_text}</p>
                        </div>
                        <div className="bg-green-50 p-3 rounded text-sm">
                          <p className="font-medium text-green-700 mb-1">Plain English</p>
                          <p className="text-green-800">{article.plain_english}</p>
                        </div>
                      </div>
                    </div>
                  )) : (
                    <div className="text-center py-12 text-gray-500">
                      <FileText className="w-12 h-12 mx-auto mb-3 text-gray-300" />
                      <p>Select a country to load DTAA articles</p>
                    </div>
                  )}
                </div>
              </div>
            )}

            {activeTab === "setup" && (
              <div className="space-y-4">
                <h2 className="text-lg font-semibold mb-4">Subsidiary Setup Checklist</h2>
                <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-4 mb-4">
                  <div className="flex items-start gap-3">
                    <AlertTriangle className="w-5 h-5 text-yellow-600 mt-0.5" />
                    <div>
                      <p className="font-medium text-yellow-800">Foreign Direct Investment (FDI) Route</p>
                      <p className="text-sm text-yellow-700 mt-1">
                        Most sectors allow 100% automatic route FDI. Sectors like defense, telecom, and aviation require government approval.
                      </p>
                    </div>
                  </div>
                </div>
                <div className="space-y-2">
                  {[
                    "Reserve unique name (RUN form on MCA portal)",
                    "Draft MOA and AOA with FDI-compliant objects",
                    "File SPICe+ Part B for incorporation",
                    "Obtain PAN and TAN (auto-generated)",
                    "Open bank account and remit share capital",
                    "File FC-GPR within 30 days of allotment",
                    "Register for GST if turnover exceeds threshold",
                    "Obtain IEC for import/export activities"
                  ].map((item, i) => (
                    <div key={i} className="flex items-center gap-3 p-3 bg-white border border-gray-200 rounded-lg hover:bg-gray-50">
                      <ChevronRight className="w-4 h-4 text-gray-400" />
                      <span className="text-sm text-gray-700">{item}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
