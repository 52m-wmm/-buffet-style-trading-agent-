import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api is proxied to the FastAPI server started with:
//   uvicorn buffett_agent.api:app --reload --port 8000
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
