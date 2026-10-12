import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.39.3'; // 🔴 Added Supabase Client

// 🔴 Security Update: Strict CORS Configuration
const ALLOWED_ORIGINS = [
  'http://localhost:3000',
  'http://127.0.0.1:5500',
  'https://steplk.com',
  'https://www.steplk.com'
];

function getCorsHeaders(reqOrigin: string | null) {
  const origin = ALLOWED_ORIGINS.includes(reqOrigin || '') ? reqOrigin : ALLOWED_ORIGINS[2];
  return {
    'Access-Control-Allow-Origin': origin || '*', 
    'Access-Control-Allow-Methods': 'POST, OPTIONS',
    'Access-Control-Allow-Headers': 'authorization, x-client-info, apikey, content-type',
  };
}

const DEFAULT_CHAIN = [
  "gemini-3.5-flash",       
  "gemini-3.8-flash",       
  "gemini-3.6-flash",       
  "gemini-3.5-flash-lite"   
];

serve(async (req) => {
  const reqOrigin = req.headers.get('origin');
  const corsHeaders = getCorsHeaders(reqOrigin);

  // CORS Preflight
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    // 🔴 Security Update: Strict JWT Auth Check via Supabase
    const authHeader = req.headers.get('authorization');
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      console.warn("Unauthorized request attempt (Missing or Invalid token format).");
      return new Response(JSON.stringify({ error: "Unauthorized access." }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    // Initialize Supabase client to verify the token
    const supabaseUrl = Deno.env.get('SUPABASE_URL') ?? '';
    const supabaseAnonKey = Deno.env.get('SUPABASE_ANON_KEY') ?? '';
    const supabase = createClient(supabaseUrl, supabaseAnonKey);

    const token = authHeader.replace('Bearer ', '');
    
    // Validating the JWT Token
    const { data: { user }, error: authError } = await supabase.auth.getUser(token);
    
    if (authError || !user) {
      console.warn("Unauthorized request attempt (Invalid or Expired JWT Token).", authError?.message);
      return new Response(JSON.stringify({ error: "Invalid token. Unauthorized access." }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    const GEMINI_API_KEY = Deno.env.get("GEMINI_API_KEY");
    if (!GEMINI_API_KEY) {
      console.error("CRITICAL: GEMINI_API_KEY is missing in env vars.");
      throw new Error("Internal Configuration Error.");
    }

    const requestData = await req.json();
    let base64Data = requestData.base64Data;
    const mimeType = requestData.mimeType || "image/jpeg";

    if (!base64Data) {
      console.warn("Bad Request: No image data provided.");
      return new Response(JSON.stringify({ error: "Invalid request payload." }), { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }
    
    if (base64Data.includes(',')) base64Data = base64Data.split(',')[1];

    const geminiPayload = {
      contents: [
        {
          parts: [
            {
              text: `Analyze this job advertisement carefully. 
First, determine if the advertiser is a direct employer OR a third-party recruitment/manpower agency hiring on behalf of another client (e.g., 'Our client is looking for', 'Consultancies', 'Headhunters').
Return ONLY a valid JSON object matching this schema exactly, with NO markdown formatting.
{
  "is_agency": boolean,
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
        console.error(`Gemini API Error (${modelName}):`, data.error?.message);
      } catch (fetchError: any) {
        console.error(`Fetch Error (${modelName}):`, fetchError.message);
      }
    }

    console.error("All AI models in the chain failed.");
    return new Response(JSON.stringify({ error: "AI Processing Failed. Please try again later." }), { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } });

  } catch (error: any) {
    console.error("Edge Function Unhandled Error:", error.message);
    return new Response(JSON.stringify({ error: "Internal Server Error." }), { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } });
  }
});
