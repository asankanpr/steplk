import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

const DEFAULT_CHAIN = [
  "gemini-3.5-flash",       
  "gemini-3.8-flash",       
  "gemini-3.6-flash",       
  "gemini-3.5-flash-lite"   
];

serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    if (!GEMINI_API_KEY) throw new Error("API Key is missing.");

    const requestData = await req.json();
    let base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) throw new Error("No image data provided.");
    if (base64Data.includes(',')) base64Data = base64Data.split(',')[1];

    const geminiPayload = {
      contents: [
        {
          parts: [
            {
              // 🔴 මෙන්න අලුත්ම AI Deep Filter Prompt එක 🔴
              text: `Analyze this job advertisement carefully. 
First, determine if the advertiser is a direct employer OR a third-party recruitment/manpower agency hiring on behalf of another client (e.g., 'Our client is looking for', 'Consultancies', 'Headhunters').
Return ONLY a valid JSON object matching this schema exactly, with NO markdown formatting.
{
  "is_agency": boolean, // MUST BE true if it's a manpower/recruitment agency, false if direct employer
  "title": "Exact job title",
  "org": "Organization name",
  "meq": "One of: NONE, OL, AL, NVQ, DEGREE",
  "salary": "Salary if mentioned, else ''",
  "closing": "YYYY-MM-DD if mentioned, else ''",
  "qualifications": "Qualifications",
  "contact": "Contact number"
}`
            },
            { inline_data: { mime_type: mimeType, data: base64Data } }
          ]
        }
      ],
      generationConfig: { temperature: 0.1 }
    };

    let lastError = null;

    for (let i = 0; i < DEFAULT_CHAIN.length; i++) {
      const modelName = DEFAULT_CHAIN[i];
      const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${GEMINI_API_KEY}`;
      
      try {
        console.log(`🤖 Requesting Gemini (${modelName})...`);
        const response = await fetch(apiUrl, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(geminiPayload)
        });

        const data = await response.json();
        if (response.ok) {
          return new Response(JSON.stringify(data), { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } });
        }
        lastError = data.error?.message || `Model ${modelName} rejected request.`;
      } catch (fetchError: any) {
        lastError = fetchError.message;
      }
    }

    throw new Error(`All models failed: ${lastError}`);

  } catch (error: any) {
    return new Response(JSON.stringify({ error: error.message }), { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } });
  }
});
