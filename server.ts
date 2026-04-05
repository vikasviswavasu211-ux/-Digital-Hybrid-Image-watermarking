import express from "express";
import { createServer as createViteServer } from "vite";
import { createProxyMiddleware } from "http-proxy-middleware";
import { spawn } from "child_process";
import path from "path";
import fs from "fs";

export const app = express();

async function startServer() {
  const PORT = 3000;
  const FLASK_PORT = 5001; 

  // Start Flask Backend
  console.log("Starting Flask backend on port", FLASK_PORT);
  
  // Try python3 first, then python
  let pythonCmd = "python3";
  try {
    // We can't easily check with exec here, so we just try to spawn
  } catch (e) {}

  const flask = spawn(pythonCmd, ["backend/app.py"], {
    env: { ...process.env, PORT: FLASK_PORT.toString() },
    stdio: "inherit"
  });

  flask.on("error", (err) => {
    console.error("Failed to start Flask backend with python3, trying python...", err);
    const flask2 = spawn("python", ["backend/app.py"], {
      env: { ...process.env, PORT: FLASK_PORT.toString() },
      stdio: "inherit"
    });
    flask2.on("error", (err2) => {
      console.error("Failed to start Flask backend with python:", err2);
    });
    
    process.on("SIGINT", () => {
      flask2.kill();
      process.exit();
    });
    process.on("SIGTERM", () => {
      flask2.kill();
      process.exit();
    });
  });

  process.on("SIGINT", () => {
    flask.kill();
    process.exit();
  });
  process.on("SIGTERM", () => {
    flask.kill();
    process.exit();
  });

  // Proxy API requests to Flask
  app.use("/api", createProxyMiddleware({
    target: `http://127.0.0.1:${FLASK_PORT}`,
    changeOrigin: true,
    on: {
      proxyRes: (proxyRes, req, res) => {
        // Add CORS headers just in case
        res.setHeader('Access-Control-Allow-Origin', '*');
      },
      error: (err, req, res) => {
        console.error("Proxy Error:", err);
        if (res && 'headersSent' in res && !res.headersSent) {
          (res as any).writeHead(503, { 'Content-Type': 'application/json' });
          (res as any).end(JSON.stringify({ 
            error: "Backend is starting up or unavailable", 
            details: "The Python Flask server is still initializing. Please wait 5-10 seconds and refresh." 
          }));
        }
      }
    }
  }));

  // Serve Frontend
  if (process.env.NODE_ENV !== "production") {
    const vite = await createViteServer({
      server: { middlewareMode: true },
      appType: "spa",
    });
    app.use(vite.middlewares);
  } else {
    const distPath = path.join(process.cwd(), "dist");
    if (fs.existsSync(distPath)) {
      app.use(express.static(distPath));
      app.get("*", (req, res) => {
        res.sendFile(path.join(distPath, "index.html"));
      });
    } else {
      app.get("*", (req, res) => {
        res.status(404).send("Production build not found. Run 'npm run build' first.");
      });
    }
  }

  app.listen(PORT, "0.0.0.0", () => {
    console.log(`Unified server running on http://localhost:${PORT}`);
    console.log(`Proxying /api to Flask on port ${FLASK_PORT}`);
  });
}

// Start server if not in Vercel environment
if (process.env.VERCEL !== "1") {
  startServer().catch(err => {
    console.error("Failed to start unified server:", err);
  });
}

export default app;
