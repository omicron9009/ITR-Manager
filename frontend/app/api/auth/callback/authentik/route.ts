import { NextRequest, NextResponse } from "next/server";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const code = searchParams.get("code");
  const state = searchParams.get("state");

  console.log("--- Auth Callback Triggered ---");
  console.log("Code received:", code ? "Yes (Masked)" : "No");
  console.log("State received:", state || "None");

  if (!code) {
    return NextResponse.json(
      { error: "No code provided from Authentik" },
      { status: 400 },
    );
  }

  try {
    const backendUrl = "http://localhost:8000/api/v1/auth/exchange";

    console.log(`Attempting exchange at: ${backendUrl}`);

    const response = await fetch(backendUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code }),
    });

    // Try to parse the response as JSON
    const data = await response.json();

    if (response.ok) {
      console.log("Exchange successful! Redirecting to dashboard...");
      const res = NextResponse.redirect(new URL("/dashboard", request.url));

      // Store the token in an httpOnly cookie for security
      res.cookies.set("token", data.access_token, {
        httpOnly: true,
        secure: process.env.NODE_ENV === "production",
        sameSite: "lax",
        path: "/",
      });
      return res;
    }

    // IF WE REACH HERE, FASTAPI REJECTED US
    console.error("--- FastAPI Exchange Failed ---");
    console.error("Status:", response.status);
    console.error("Error Detail from Backend:", data);

    return NextResponse.json(
      {
        error: "Auth exchange failed",
        backend_status: response.status,
        reason: data.detail || data, // Show the actual reason from FastAPI
      },
      { status: 401 },
    );
  } catch (error: any) {
    console.error("--- Fetch Error (Is FastAPI running?) ---");
    console.error(error.message);

    return NextResponse.json(
      { error: "Internal Server Error", message: error.message },
      { status: 500 },
    );
  }
}
