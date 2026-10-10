import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

serve(async (req) => {
  // CORS Preflight Request
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  try {
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    
    if (!GEMINI_API_KEY) {
      throw new Error("GEMINI_API_KEY is missing in Edge Function secrets.");
    }

    const requestData = await req.json();
    const base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) {
      throw new Error("No image data received.");
    }

    // Prepare Payload for Gemini
    const geminiPayload = {
      contents: [{
        parts: [
          {
            inlineData: {
              mimeType: mimeType,
              data: base64Data
            }
          },
          {
            text: `Extract details from this job advertisement.
Return ONLY a valid JSON object matching this schema exactly, do not add any markdown formatting like \`\`\`json:
{
  "title": "Job title",
  "org": "Organization name",
  "meq": "One of: NONE, OL, AL, NVQ, DEGREE",
  "salary": "Salary if mentioned, else ''",
  "closing": "YYYY-MM-DD if mentioned, else ''",
  "qualifications": "Qualifications",
  "contact": "Contact number"
}`
          }
        ]
      }],
      generationConfig: {
        temperature: 0.1,
      }
    };

    // Call Gemini API (using gemini-1.5-flash as it is faster and cheaper)
    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${GEMINI_API_KEY}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(geminiPayload)
    });

    const geminiData = await response.json();

    // Catch API Errors (This will now show exactly why Gemini failed)
    if (!response.ok) {
      const apiErrorMsg = geminiData.error?.message || "Unknown Gemini API Error";
      console.error("Gemini API Error Details:", geminiData);
      return new Response(
        JSON.stringify({ error: `Gemini API Failed: ${apiErrorMsg}` }),
        { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

    // Success response
    return new Response(
      JSON.stringify(geminiData),
      { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error: any) {
    console.error("Edge Function Caught Error:", error.message);
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
