import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

// 1. Define the High-Resiliency Fallback Model Chain
const DEFAULT_CHAIN = [
  "gemini-3.5-flash",       // Tier 1: Fast & reliable (Best choice for standard OCR)
  "gemini-3.8-flash",       // Tier 2: High capacity backup (If 3.5 is busy)
  "gemini-3.6-flash",       // Tier 3: High reasoning backup
  "gemini-3.5-flash-lite"   // Tier 4: Highest rate limit buffer (Last resort)
];

serve(async (req) => {
  // CORS Preflight Request
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    if (!GEMINI_API_KEY) {
      throw new Error("API Key is missing in Supabase Edge Function Secrets.");
    }

    const requestData = await req.json();
    let base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) {
      throw new Error("No image data provided.");
    }

    // Clean Base64 string if it has a data URI prefix
    if (base64Data.includes(',')) {
      base64Data = base64Data.split(',')[1];
    }

    const geminiPayload = {
      contents: [
        {
          parts: [
            {
              text: `Extract details from this job advertisement.
Return ONLY a valid JSON object matching this schema exactly, with NO markdown formatting.
{
  "title": "Exact job title",
  "org": "Organization name",
  "meq": "One of: NONE, OL, AL, NVQ, DEGREE",
  "salary": "Salary if mentioned, else ''",
  "closing": "YYYY-MM-DD if mentioned, else ''",
  "qualifications": "Qualifications",
  "contact": "Contact number"
}`
            },
            {
              inline_data: {
                mime_type: mimeType,
                data: base64Data
              }
            }
          ]
        }
      ],
      generationConfig: {
        temperature: 0.1
      }
    };

    let lastError = null;

    // 2. Loop through the Model Chain (Fallback Logic)
    for (let i = 0; i < DEFAULT_CHAIN.length; i++) {
      const modelName = DEFAULT_CHAIN[i];
      const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/${modelName}:generateContent?key=${GEMINI_API_KEY}`;
      
      try {
        console.log(`🤖 Requesting Gemini (${modelName})...`);
        
        const response = await fetch(apiUrl, {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify(geminiPayload)
        });

        const data = await response.json();

        // If response is OK, return data immediately (breaking the loop)
        if (response.ok) {
          console.log(`✅ Extraction Successful with Model [${modelName}]`);
          return new Response(
            JSON.stringify(data),
            { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
          );
        }

        // If NOT ok, capture the error and let the loop continue to the next model
        console.warn(`⚠️ Model [${modelName}] failed or rejected the request. Details:`, data);
        lastError = data.error?.message || `Model ${modelName} rejected request.`;
        
      } catch (fetchError: any) {
        // Network or parsing errors
        console.warn(`⚠️ Fetch Error with [${modelName}]: ${fetchError.message}`);
        lastError = fetchError.message;
      }
    }

    // 3. If the loop finishes and we are here, ALL models failed
    throw new Error(`❌ All Gemini models in DEFAULT_CHAIN failed! Last error: ${lastError}`);

  } catch (error: any) {
    console.error("Edge Function Caught Critical Error:", error.message);
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
