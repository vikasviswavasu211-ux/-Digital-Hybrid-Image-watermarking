# Production Deployment Guide

This guide outlines how to deploy the Image Watermarking application (Hybrid DWT-SVD) to production using **Vercel** for the frontend and **Render** (or Railway) for the backend.

## 1. Backend Deployment (Render / Railway)

The backend is a Python Flask application located in the `/backend` directory.

### Steps for Render:
1.  **Create a new Web Service** on Render.
2.  **Connect your GitHub repository**.
3.  **Root Directory**: `backend` (or leave as root and set the build/start commands accordingly).
4.  **Environment**: `Python 3`.
5.  **Build Command**: `pip install -r requirements.txt`.
6.  **Start Command**: `gunicorn app:app`.
7.  **Environment Variables**:
    *   `PORT`: `10000` (Render will provide this, but you can set it).
    *   `PYTHON_VERSION`: `3.10.x` (recommended).

### Steps for Railway:
1.  **New Project** -> **Deploy from GitHub repo**.
2.  Railway will automatically detect the `Procfile` in `/backend`.
3.  Ensure the root directory for the service is set to `/backend`.

---

## 2. Frontend Deployment (Vercel)

The frontend is a React application built with Vite.

### Steps:
1.  **Create a new Project** on Vercel.
2.  **Connect your GitHub repository**.
3.  **Framework Preset**: `Vite`.
4.  **Build Command**: `npm run build`.
5.  **Output Directory**: `dist`.
6.  **Environment Variables**:
    *   `VITE_API_BASE_URL`: The URL of your deployed backend (e.g., `https://your-backend.onrender.com`). **Do not include a trailing slash.**

---

## 3. Local Development (AI Studio)

In the AI Studio environment, the application runs using a unified server (`server.ts`) that proxies requests to the Flask backend.

*   **Vite Dev Server**: Runs on port 3000 (via Express middleware).
*   **Flask Backend**: Runs on port 5001 (proxied via Express).

### Troubleshooting "Failed to fetch":
*   Ensure the Flask backend has started (check terminal logs).
*   If you see "Backend is starting up", wait 5-10 seconds and refresh.
*   Check that `python3` is available in your environment.

## 4. Production-Grade Features Added:
*   **Stateless Backend**: No global variables, safe for horizontal scaling.
*   **Memory Management**: Explicit `gc.collect()` and image resizing (max 512x512) to prevent OOM errors.
*   **Robust Frontend**: `fetchWithRetry` logic, simultaneous request prevention, and loading/error UI.
*   **Unified Proxy**: Local development matches production architecture.
