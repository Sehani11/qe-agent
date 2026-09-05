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
  // /training and /comparison merged into /fine-tune. Redirected rather than
  // deleted outright so bookmarks and any link shared in a ticket still land
  // somewhere useful instead of on a 404.
  //
  // Not `permanent`: a 308 is cached hard by the browser, which is a poor trade
  // while the information architecture is still moving. A 307 costs one request
  // and can be taken back.
  async redirects() {
    return [
      { source: "/training", destination: "/fine-tune", permanent: false },
      { source: "/comparison", destination: "/fine-tune", permanent: false },
    ];
  },
};

export default nextConfig;
