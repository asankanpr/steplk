import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

serve(async (req) => {
  // 1. Handle CORS Preflight
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    if (!GEMINI_API_KEY) {
      throw new Error("API Key is missing in Supabase Edge Function Secrets.");
    }

    // 2. Parse Incoming Request from admin.html
    const requestData = await req.json();
    let base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) {
      throw new Error("No image data provided.");
    }

    // Remove data URI prefix if present (e.g., "data:image/jpeg;base64,")
    if (base64Data.includes(',')) {
      base64Data = base64Data.split(',')[1];
    }

    // 3. Construct the EXACT Gemini API Payload 
    // Notice: NO Markdown (```json) asked in prompt
    const geminiPayload = {
      contents: [
        {
          parts: [
            {
              text: `Read this job advertisement image. Extract details and return ONLY a plain JSON object. Do not include markdown tags like \`\`\`json.
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

    // 4. Send Request to Gemini API
    const apiUrl = `[https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=$](https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=$){GEMINI_API_KEY}`;
    
    const response = await fetch(apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(geminiPayload)
    });

    const data = await response.json();

    if (!response.ok) {
        console.error("Gemini Error:", data);
        throw new Error(data.error?.message || "Gemini API rejected the request.");
    }

    // 5. Return Success Response to admin.html
    return new Response(
      JSON.stringify(data),
      { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error: any) {
    console.error("Edge Function Error:", error.message);
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
