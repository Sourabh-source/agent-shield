import { NextRequest, NextResponse } from "next/server";

const BACKEND_URL = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");

async function handleProxy(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  // Await params for Next.js 15+ async route handler parameters
  const resolvedParams = await context.params;
  const pathSegments = resolvedParams?.path || [];
  const subPath = pathSegments.length > 0 ? `/${pathSegments.join("/")}` : "";

  // Prioritize incoming caller-specified X-API-Key (e.g. for multi-tenant tests),
  // otherwise fallback to server-only AGENTGUARD_API_KEY
  const incomingKey = request.headers.get("x-api-key");
  const apiKey =
    incomingKey && incomingKey.trim().length > 0
      ? incomingKey.trim()
      : process.env.AGENTGUARD_API_KEY;

  if (!apiKey || apiKey.trim().length === 0) {
    console.error(
      "[AgentGuard Proxy Error]: Server is not configured with a backend API key. " +
        "Set AGENTGUARD_API_KEY in the frontend environment (do NOT use NEXT_PUBLIC_ prefix)."
    );
    return NextResponse.json(
      {
        error: "Server misconfiguration",
        message:
          "Server is not configured with a backend API key. Set AGENTGUARD_API_KEY in the frontend environment.",
      },
      { status: 500 }
    );
  }

  // Construct target URL including any query parameters
  const search = request.nextUrl.search || "";
  const targetUrl = `${BACKEND_URL}${subPath}${search}`;

  // Forward relevant headers
  const forwardHeaders = new Headers();
  forwardHeaders.set("X-API-Key", apiKey);

  const contentType = request.headers.get("content-type");
  if (contentType) {
    forwardHeaders.set("Content-Type", contentType);
  }

  const accept = request.headers.get("accept");
  if (accept) {
    forwardHeaders.set("Accept", accept);
  }

  const correlationId = request.headers.get("x-correlation-id");
  if (correlationId) {
    forwardHeaders.set("X-Correlation-ID", correlationId);
  }

  // Forward body for mutating methods
  let body: BodyInit | undefined = undefined;
  if (request.method !== "GET" && request.method !== "HEAD") {
    const rawBody = await request.arrayBuffer();
    if (rawBody.byteLength > 0) {
      body = rawBody;
    }
  }

  try {
    const backendRes = await fetch(targetUrl, {
      method: request.method,
      headers: forwardHeaders,
      body,
      cache: "no-store",
    });

    const responseHeaders = new Headers();
    const backendContentType = backendRes.headers.get("content-type");
    if (backendContentType) {
      responseHeaders.set("content-type", backendContentType);
    }
    const backendCorrelationId = backendRes.headers.get("x-correlation-id");
    if (backendCorrelationId) {
      responseHeaders.set("x-correlation-id", backendCorrelationId);
    }

    const responseData = await backendRes.arrayBuffer();
    return new NextResponse(responseData, {
      status: backendRes.status,
      statusText: backendRes.statusText,
      headers: responseHeaders,
    });
  } catch (err: unknown) {
    const errMsg = err instanceof Error ? err.message : "Unknown error";
    console.error(`[AgentGuard Proxy Error]: Failed to reach backend at ${targetUrl}:`, errMsg);
    return NextResponse.json(
      {
        error: "Backend communication error",
        message: `Failed to contact backend service: ${errMsg}`,
      },
      { status: 502 }
    );
  }
}

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}

export async function POST(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}

export async function PUT(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}

export async function PATCH(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}

export async function DELETE(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}

export async function HEAD(
  request: NextRequest,
  context: { params: Promise<{ path: string[] }> | { path: string[] } }
) {
  return handleProxy(request, context);
}
