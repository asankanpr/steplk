import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const corsHeaders = {
  'Access-Control-Allow-Origin': '*',
  'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
};

serve(async (req) => {
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders });
  }

  try {
    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    
    if (!GEMINI_API_KEY) {
      throw new Error("GEMINI_API_KEY is not configured in Supabase Edge Function.");
    }

    const { base64Data, mimeType } = await req.json();

    if (!base64Data) {
      throw new Error("No image data provided for scanning.");
    }

    const geminiPayload = {
      contents: [{
        parts: [
          {
            inlineData: {
              mimeType: mimeType || "image/jpeg",
              data: base64Data
            }
          },
          {
            text: `
              You are an expert Sri Lankan Job Advertisement OCR Specialist.
              Extract the following details from this image and return strictly in JSON format.
              {
                "title": "Exact job title",
                "org": "Organization name",
                "meq": "One of: NONE, OL, AL, NVQ, DEGREE",
                "salary": "Salary amount or range if mentioned, else 'සඳහන් නැත'",
                "closing": "Closing date in YYYY-MM-DD if mentioned, else ''",
                "qualifications": "Qualifications in bullet points",
                "contact": "Contact number if mentioned, else ''"
              }
              Respond ONLY with the JSON object.
            `
          }
        ]
      }],
      generationConfig: {
        temperature: 0.1,
      }
    };

    const response = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent?key=${GEMINI_API_KEY}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(geminiPayload)
    });

    const geminiData = await response.json();

    if (!response.ok || geminiData.error) {
      console.error("Gemini API Error:", geminiData.error);
      throw new Error(geminiData.error?.message || "Gemini API request failed.");
    }

    return new Response(
      JSON.stringify(geminiData),
      { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );

  } catch (error: any) {
    console.error("Edge Function Exception:", error.message);
    return new Response(
      JSON.stringify({ error: error.message }),
      { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
