import { serve } from "https://deno.land/std@0.168.0/http/server.ts";
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2.39.3'; 

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

serve(async (req) => {
  const reqOrigin = req.headers.get('origin');
  const corsHeaders = getCorsHeaders(reqOrigin);

  // CORS Preflight Request
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: corsHeaders, status: 200 });
  }

  try {
    // 🔴 Security Update: Strict Auth Check (User JWT OR Service Role Key)
    const authHeader = req.headers.get('authorization');
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      console.warn("Unauthorized upload attempt (Missing or Invalid token format).");
      return new Response(JSON.stringify({ success: false, error: "Unauthorized access." }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    const token = authHeader.replace('Bearer ', '');
    const serviceRoleKey = Deno.env.get('SUPABASE_SERVICE_ROLE_KEY');
    
    let isAuthorized = false;

    // 1. Check if the token is the Server-side Service Role Key (For Cron Jobs / Python Scrapers)
    if (serviceRoleKey && token === serviceRoleKey) {
        isAuthorized = true;
    } 
    // 2. Check if the token is a Valid User JWT (For Admin Web Panel)
    else {
        const supabaseUrl = Deno.env.get('SUPABASE_URL') ?? '';
        const supabaseAnonKey = Deno.env.get('SUPABASE_ANON_KEY') ?? '';
        const supabase = createClient(supabaseUrl, supabaseAnonKey);
        
        const { data: { user }, error: authError } = await supabase.auth.getUser(token);
        if (user && !authError) {
            isAuthorized = true;
        }
    }

    if (!isAuthorized) {
      console.warn("Unauthorized request attempt (Invalid Token).");
      return new Response(JSON.stringify({ success: false, error: "Invalid token. Unauthorized access." }), { status: 401, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    const IMGBB_API_KEY = Deno.env.get("IMGBB_API_KEY");
    if (!IMGBB_API_KEY) {
      console.error("CRITICAL: IMGBB_API_KEY is missing.");
      throw new Error("Server Configuration Error.");
    }

    const requestData = await req.json();
    let base64Image = requestData.base64Image;

    if (!base64Image) {
      console.warn("Bad Request: No image data.");
      return new Response(JSON.stringify({ success: false, error: "Invalid payload." }), { status: 400, headers: { ...corsHeaders, "Content-Type": "application/json" } });
    }

    if (base64Image.includes(',')) {
      base64Image = base64Image.split(',')[1];
    }

    const formData = new FormData();
    formData.append("image", base64Image);

    // ImgBB API Request
    const response = await fetch(`https://api.imgbb.com/1/upload?key=${IMGBB_API_KEY}`, {
      method: "POST",
      body: formData,
    });

    const imgData = await response.json();

    if (imgData && imgData.success) {
      return new Response(
        JSON.stringify({ success: true, url: imgData.data.url }),
        { status: 200, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    } else {
      console.error("ImgBB Upload Failed:", imgData);
      return new Response(
        JSON.stringify({ success: false, error: "External upload service failed." }),
        { status: 502, headers: { ...corsHeaders, "Content-Type": "application/json" } }
      );
    }

  } catch (error: any) {
    console.error("Upload Edge Function Error:", error.message);
    return new Response(
      JSON.stringify({ success: false, error: "Internal Server Error." }),
      { status: 500, headers: { ...corsHeaders, "Content-Type": "application/json" } }
    );
  }
});
