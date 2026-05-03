"use client";

export default function LoginPage() {
  const handleLogin = () => {
    // These match your Authentik Provider settings
    const AUTHENTIK_BASE_URL = "http://localhost:9004";
    const CLIENT_ID = "08ESsuFamTq2bFosxaRtlts79pUUjgweRLCXIDHG";
    const REDIRECT_URI = "http://localhost:3000/api/auth/callback/authentik"; // Your NextJS backend route
    const RESPONSE_TYPE = "code";
    const SCOPE = "openid profile email";
    const state = Math.random().toString(36).substring(7);

    const authUrl =
      `${AUTHENTIK_BASE_URL}/application/o/authorize/?` +
      `client_id=${CLIENT_ID}&` +
      `redirect_uri=${encodeURIComponent(REDIRECT_URI)}&` +
      `response_type=${RESPONSE_TYPE}&` +
      `state=${state}&` +
      `scope=${encodeURIComponent(SCOPE)}`;

    // Redirect the user to Authentik
    window.location.href = authUrl;
  };

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-100">
      <div className="p-8 bg-white shadow-md rounded-lg text-center">
        <h1 className="text-2xl font-bold mb-6">ITR Filing Platform</h1>
        <p className="text-gray-600 mb-8">Secure Login via Authentik</p>
        <button
          onClick={handleLogin}
          className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-6 rounded transition-colors"
        >
          Sign In
        </button>
      </div>
    </div>
  );
}
