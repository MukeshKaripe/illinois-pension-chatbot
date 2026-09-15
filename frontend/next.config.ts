import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // A static export, because there is nothing here for a server to do.
  //
  // No server actions, no route handlers, no `cookies()` -- `src/lib/api.ts`
  // talks to the backend from the browser and carries the credential itself.
  // Exporting makes the frontend a folder of files, which is what lets it sit
  // on the Static Web Apps free tier with a CDN and managed TLS instead of
  // needing a Node runtime of its own.
  //
  // The consequence to remember: `NEXT_PUBLIC_API_URL` is baked in at build
  // time, not read at run time. Pointing this build at a different backend
  // means rebuilding it, which is why `deploy.ps1` creates the backend first
  // and passes its address into this build rather than the other way round.
  output: "export",

  // The export writes plain files, so there is no image optimiser to run.
  images: { unoptimized: true },
};

export default nextConfig;
