import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react-swc";
import path from "path";
import { componentTagger } from "lovable-tagger"; // <--- Essa linha é essencial!

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  // Carrega variáveis de ambiente (prioriza .env.local, depois .env)
  const env = loadEnv(mode, process.cwd(), '');
  
  // Define o alvo da API: Usa a variável VITE_API_URL se existir, senão usa o padrão do Docker
  const apiTarget = env.VITE_API_URL || 'http://api:8000';

  console.log(`[Vite Proxy] Redirecionando /api para: ${apiTarget}`);

  return {
    server: {
      host: "0.0.0.0",
      port: 5173,
      watch: {
        usePolling: true,
      },
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
      allowedHosts: ['twotask.mdr.local']
    },
    appType: 'spa',
    plugins: [
      react(), 
      mode === "development" && componentTagger() // O erro acontecia aqui pois faltava o import
    ].filter(Boolean),
    resolve: {
      alias: {
        "@": path.resolve(__dirname, "./src"),
      },
    },
    build: {
      rollupOptions: {
        input: {
          // App principal + página de retorno do MSAL (2ª entry, bundlada — roda
          // handleRedirectPromise no popup e devolve o token via BroadcastChannel;
          // as páginas de login da Microsoft mandam COOP e cortam o window.opener).
          main: path.resolve(__dirname, "index.html"),
          "msal-redirect": path.resolve(__dirname, "msal-redirect.html"),
        },
      },
    },
  };
});