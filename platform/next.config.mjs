/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  eslint: {
    // Linting runs in CI outside the build; keep builds deterministic.
    ignoreDuringBuilds: true,
  },
  transpilePackages: ["three"],
};

export default nextConfig;
