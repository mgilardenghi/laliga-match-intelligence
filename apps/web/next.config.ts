import path from "node:path";
import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // The API client ships TypeScript sources, so Next compiles the workspace
  // package as part of the app build instead of leaving it to node resolution.
  transpilePackages: ["@lmi/api-client"],
  // Trace from the repository root so the standalone output contains the
  // workspace node_modules that apps/web/server.js needs inside the image.
  outputFileTracingRoot: path.join(__dirname, "..", ".."),
};

export default nextConfig;
