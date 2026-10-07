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
    // Supabase Secret එකෙන් හෝ නැත්නම් Hardcoded Fallback එකෙන් Key එක ලබාගැනීම
    const IMGBB_API_KEY = Deno.env.get("IMGBB_API_KEY") || "a6b8a783f1c3a779ba56333049e2dc2c";

    const requestData = await req.json();
    let base64Image = requestData.base64Image;

    if (!base64Image) {
      throw new Error("No image data provided in the request.");
    }

    // Base64 string එකේ prefix එකක් (data:image/...;base64,) තිබේ නම් එය ඉවත් කිරීම
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
        { 
          status: 200, 
          headers: { ...corsHeaders, "Content-Type": "application/json" } 
        }
      );
    } else {
      console.error("ImgBB API Response Error:", imgData);
      throw new Error(imgData?.error?.message || "ImgBB upload failed.");
    }

  } catch (error: any) {
    console.error("Edge Function Error:", error.message);
    return new Response(
      JSON.stringify({ success: false, error: error.message }),
      { 
        status: 400, 
        headers: { ...corsHeaders, "Content-Type": "application/json" } 
      }
    );
  }
});
