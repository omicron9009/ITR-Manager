export default function LoginPage() {
  // Replace these with your actual Authentik details
  const AUTHENTIK_BASE_URL = "http://localhost:9000";
  const CLIENT_ID = "your-client-id";
  const REDIRECT_URI = "http://localhost:3000/api/auth/callback"; // Your app's callback

  const authUrl = `${AUTHENTIK_BASE_URL}/application/o/authorize/?client_id=${CLIENT_ID}&response_type=code&redirect_uri=${encodeURIComponent(REDIRECT_URI)}&scope=openid+email+profile`;

  return (
    <div className="flex flex-col items-center justify-center min-h-screen bg-gray-50">
      <div className="p-8 bg-white shadow-xl rounded-2xl w-full max-w-md border border-gray-100">
        <h1 className="text-2xl font-bold text-gray-800 mb-2">Welcome Back</h1>
        <p className="text-gray-500 mb-8">
          Please sign in to access your dashboard.
        </p>

        <a
          href={authUrl}
          className="w-full flex items-center justify-center py-3 px-4 bg-orange-600 hover:bg-orange-700 text-white font-semibold rounded-lg transition-colors duration-200"
        >
          Sign in with Authentik
        </a>

        <div className="mt-6 text-center">
          <p className="text-xs text-gray-400">
            Secure authentication powered by Authentik SSO
          </p>
        </div>
      </div>
    </div>
  );
}
