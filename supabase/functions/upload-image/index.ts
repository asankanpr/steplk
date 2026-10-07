// supabase/functions/upload-image/index.ts
import { serve } from "https://deno.land/std@0.168.0/http/server.ts";

const IMGBB_API_KEY = Deno.env.get("IMGBB_API_KEY");

serve(async (req) => {
  // CORS Headers
  if (req.method === 'OPTIONS') {
    return new Response('ok', { headers: { 'Access-Control-Allow-Origin': '*' } })
  }

  try {
    const { base64Image } = await req.json();

    if (!base64Image) {
      throw new Error("No image data provided.");
    }

    const formData = new FormData();
    formData.append("image", base64Image);

    const response = await fetch(`https://api.imgbb.com/1/upload?key=${IMGBB_API_KEY}`, {
      method: "POST",
      body: formData,
    });

    const imgData = await response.json();

    if (imgData && imgData.success) {
      return new Response(
        JSON.stringify({ success: true, url: imgData.data.url }),
        { headers: { "Content-Type": "application/json", 'Access-Control-Allow-Origin': '*' } }
      );
    } else {
      throw new Error("ImgBB upload failed.");
    }
  } catch (error) {
    return new Response(
      JSON.stringify({ success: false, error: error.message }),
      { status: 400, headers: { "Content-Type": "application/json", 'Access-Control-Allow-Origin': '*' } }
    );
  }
});
