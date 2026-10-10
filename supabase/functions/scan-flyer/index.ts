// No import needed for Deno.serve
const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Methods': 'POST, OPTIONS',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

Deno.serve(async (req) => {
  // 1. CORS Preflight (OPTIONS request) - MUST return immediately
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    // 2. Get API Key
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    if (!GEMINI_API_KEY) {
      throw new Error("GEMINI_API_KEY is missing in Edge Function environment.");
    }

    // 3. Parse Request
    const requestData = await req.json();
    const base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) {
      throw new Error("No image data provided in the request body.");
    }

    // 4. Prepare Gemini Request Payload
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
Return ONLY a valid JSON object matching this schema exactly, with NO markdown formatting (do not use \`\`\`json):
{
  "title": "Exact job title",
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

    const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${GEMINI_API_KEY}`;
    
    // 5. Fetch from Gemini
    const response = await fetch(apiUrl, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(geminiPayload)
    });

    const geminiData = await response.json();

    // 6. Handle Gemini Errors
    if (!response.ok) {
        console.error("Gemini Response:", geminiData);
        throw new Error(geminiData.error?.message || "Gemini API failed.");
    }

    // 7. Success Return
    return new Response(
      JSON.stringify(geminiData),
      { 
        status: 200, 
        headers: { ...corsHeaders, "Content-Type": "application/json" } 
      }
    );

  } catch (error: any) {
    console.error("Edge Function Caught Error:", error.message);
    // Even if it fails, WE MUST return corsHeaders, otherwise the browser hides the real error behind a CORS error.
    return new Response(
      JSON.stringify({ error: error.message }),
      { 
        status: 400, 
        headers: { ...corsHeaders, "Content-Type": "application/json" } 
      }
    );
  }
});
