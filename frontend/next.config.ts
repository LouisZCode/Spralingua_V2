import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Emit a self-contained server build (.next/standalone) so the Docker runtime
  // image ships just the server + a trimmed node_modules instead of the full one.
  output: "standalone",
};

export default nextConfig;
