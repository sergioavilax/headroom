import type { NextConfig } from "next";

/**
 * `standalone` output is what makes the runtime image small: `next build` emits a
 * server plus exactly the `node_modules` it imports, so the shipped layer carries no
 * devDependencies and no build toolchain. The same trade the gateway's Dockerfile makes
 * one language over.
 *
 * Nothing here reaches the network at build time — no remote fonts, no remote images, no
 * data fetching in a server component. That is deliberate: CI builds this image with no
 * gateway, no database, and no keys anywhere (BUILD_PLAN §0.2 invariant 4), and a build
 * that quietly needed a running backend would only fail once somebody else tried it.
 */
const nextConfig: NextConfig = {
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
};

export default nextConfig;
