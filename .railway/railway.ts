import { bucket, defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const PostgresZ4Hi = postgres("Postgres-z4Hi", { region: "europe-west4-drams3a" });
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "europe-west4-drams3a", sizeMB: 5000 });
  const voice = bucket("voice", { region: "ams" });
  const audio = bucket("audio", { region: "ams" });
  const frontend = service("frontend", {
    source: github("LouisZCode/Spralingua_V2", { checkSuites: true, rootDirectory: "/frontend" }),
    replicas: { "europe-west4-drams3a": 1 },
    domains: ["spralingua.com", "www.spralingua.com"],
    env: { NEXT_PUBLIC_AMPLITUDE_API_KEY: preserve(), NEXT_PUBLIC_API_URL: preserve(), NEXT_PUBLIC_GOOGLE_CLIENT_ID: preserve(), NEXT_PUBLIC_META_PIXEL_ID: preserve(), NEXT_PUBLIC_SENTRY_DSN: preserve(), NEXT_PUBLIC_SENTRY_ENVIRONMENT: preserve() },
  });
  const backend = service("backend", {
    source: github("LouisZCode/Spralingua_V2", { checkSuites: true }),
    start: "",
    healthcheck: "/health",
    replicas: { "europe-west4-drams3a": 1 },
    deploy: { preDeployCommand: ["alembic upgrade head"] },
    env: { ALLOWED_ORIGINS: preserve(), AMPLITUDE_AI_API_KEY: preserve(), APP_ENV: preserve(), AZURE_SPEECH_KEY: preserve(), AZURE_SPEECH_REGION: preserve(), BUCKET_ACCESS_KEY_ID: preserve(), BUCKET_ENDPOINT: preserve(), BUCKET_NAME: preserve(), BUCKET_REGION: preserve(), BUCKET_SECRET_ACCESS_KEY: preserve(), CEREBRAS_API_KEY: preserve(), DATABASE_URL: preserve(), DB_MAX_OVERFLOW: preserve(), DB_POOL_SIZE: preserve(), DEEPGRAM_API_KEY: preserve(), FRONTEND_BASE_URL: preserve(), GOOGLE_CLIENT_ID: preserve(), JWT_SECRET: preserve(), LANGFUSE_BASE_URL: preserve(), LANGFUSE_PUBLIC_KEY: preserve(), LANGFUSE_SECRET_KEY: preserve(), LANGFUSE_TRACING_ENVIRONMENT: preserve(), MINIMAX_API_KEY: preserve(), MINIMAX_GROUP_ID: preserve(), OPENROUTER_API_KEY: preserve(), RAILWAY_DEPLOYMENT_DRAINING_SECONDS: preserve(), SENTRY_DSN: preserve(), SENTRY_ENVIRONMENT: preserve(), STRIPE_AUTOMATIC_TAX: preserve(), STRIPE_BASIC_PRICE_ID: preserve(), STRIPE_PREMIUM_PRICE_ID: preserve(), STRIPE_SECRET_KEY: preserve(), STRIPE_TOPUP_PRICE_ID: preserve(), STRIPE_WEBHOOK_SECRET: preserve(), VOICE_BUCKET_ACCESS_KEY_ID: preserve(), VOICE_BUCKET_ENDPOINT: preserve(), VOICE_BUCKET_NAME: preserve(), VOICE_BUCKET_REGION: preserve(), VOICE_BUCKET_SECRET_ACCESS_KEY: preserve() },
  });

  return project("Spralingua", {
    resources: [PostgresZ4Hi, frontend, backend, postgresVolume, voice, audio],
  });
});
