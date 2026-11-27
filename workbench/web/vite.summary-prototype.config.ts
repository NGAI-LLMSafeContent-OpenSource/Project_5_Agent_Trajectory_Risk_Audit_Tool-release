// THROWAWAY: reuse the current app; proxy reads only, never start a new audit.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react(), {name:"prototype-read-only",configureServer(server){server.middlewares.use((req,res,next)=>{if(req.url?.startsWith('/api')&&req.method!=='GET'){res.statusCode=405;res.end('Read-only prototype');return;}next();});}}],
  server:{host:"127.0.0.1",port:8771,strictPort:true,proxy:{"/api":"http://platform.example.invalid:5861"},fs:{allow:["..","../.."]}},
});
