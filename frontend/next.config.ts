import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Allow images from any domain (for GitHub avatars etc.)
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
  // Expose backend URL to client components
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000",
  },
};

export default nextConfig;
