import type { NextConfig } from "next";

// One-time startup check for server-only backend API key
if (typeof process !== "undefined" && process.env.NODE_ENV !== "test") {
  if (!process.env.AGENTGUARD_API_KEY) {
    console.warn(
      "\n⚠️  [AgentGuard Auth Warning]: AGENTGUARD_API_KEY is not set in the environment.\n" +
        "   Proxy requests to the backend will fail with HTTP 500.\n" +
        "   Set AGENTGUARD_API_KEY in frontend/.env.local (do NOT prefix with NEXT_PUBLIC_).\n"
    );
  }
}

const nextConfig: NextConfig = {
  // Allow images from any domain (for GitHub avatars etc.)
  images: {
    remotePatterns: [{ protocol: "https", hostname: "**" }],
  },
};

export default nextConfig;
