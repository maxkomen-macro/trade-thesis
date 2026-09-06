import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Node's process without pulling in @types/node for one env lookup.
declare const process: { env: Record<string, string | undefined> };

// Port 5174 and API port 8001 are deliberately different from Radar (5173 / 8000)
// so both projects can run side by side. VITE_API_PROXY points a second dev instance at another API port.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    proxy: {
      "/api": { target: process.env.VITE_API_PROXY ?? "http://127.0.0.1:8001", changeOrigin: true },
    },
  },
});
