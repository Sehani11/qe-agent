import type { NextConfig } from "next";

// Extract the host from NEXT_PUBLIC_SITE_URL so Next.js allows Server Actions
// from the CloudFront domain. Without this, Next.js 15's CSRF guard rejects
// actions when x-forwarded-host doesn't match the Origin header.
const siteHost = (process.env.NEXT_PUBLIC_SITE_URL ?? "")
  .replace(/^https?:\/\//, "")
  .replace(/\/$/, "");

const nextConfig: NextConfig = {
  output: "standalone",
  ...(siteHost && {
    serverActions: {
      allowedOrigins: [siteHost],
    },
  }),
};

export default nextConfig;
