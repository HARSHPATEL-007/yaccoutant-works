/** @type {import('next').NextConfig} */
const nextConfig = {
  experimental: {
    appDir: true,
  },
  async rewrites() {
    return [
      {
        source: '/api/v1/:path*',
        destination: `${process.env.API_GATEWAY_URL}/api/v1/:path*`,
      },
    ];
  },
  images: {
    domains: ['localhost'],
  },
};

export default nextConfig;