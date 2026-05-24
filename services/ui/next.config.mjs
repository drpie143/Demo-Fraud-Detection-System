/** @type {import('next').NextConfig} */
const nextConfig = {
  images: {
    unoptimized: true,
  },
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL || process.env.NEXT_PUBLIC_BACKEND_URL || 'http://localhost:8000';
    return [
      {
        source: '/api/health',
        destination: `${backendUrl}/health`,
      },
      {
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
}

export default nextConfig

