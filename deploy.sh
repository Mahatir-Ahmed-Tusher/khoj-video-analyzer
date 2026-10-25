#!/usr/bin/env bash
# ==============================================================================
# Khoj Video Analyzer - 1-Click Deployment Script for Google Cloud Shell
# ==============================================================================
set -e

SERVICE_NAME="khoj-video-analyzer"
REGION="us-central1" # Recommended region with 100% Always-Free tier coverage
DEFAULT_PROJECT="gen-lang-client-0825088284"

echo "=========================================================="
echo " Starting Deployment for: $SERVICE_NAME"
echo " Target Region: $REGION"
echo "=========================================================="

# 0. Ensure GCP project is set
CURRENT_PROJECT=$(gcloud config get-value project 2>/dev/null || true)
if [ -z "$CURRENT_PROJECT" ] || [ "$CURRENT_PROJECT" = "(unset)" ]; then
    echo "--> No GCP project set. Setting project to: $DEFAULT_PROJECT..."
    gcloud config set project "$DEFAULT_PROJECT"
else
    echo "--> Using active GCP project: $CURRENT_PROJECT"
fi

# 1. Enable required Google Cloud APIs
echo "--> Enabling Cloud Run and Cloud Build APIs..."
gcloud services enable run.googleapis.com cloudbuild.googleapis.com

# 2. Check for .env file or environment variables
ENV_FLAGS=""
if [ -f ".env" ]; then
    echo "--> Detected local .env file. Passing environment variables to Cloud Run..."
    # Filter out comments, blank lines, and reserved Cloud Run variables (PORT)
    VARS=$(grep -v '^#' .env | grep -v '^$' | grep -v '^PORT=' | paste -sd "," -)
    if [ -n "$VARS" ]; then
        ENV_FLAGS="--set-env-vars=$VARS"
    fi
fi

# 3. Build and deploy container directly to Cloud Run
echo "--> Building and deploying container to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
  --source . \
  --region "$REGION" \
  --platform managed \
  --memory 4Gi \
  --cpu 2 \
  --timeout 900 \
  --concurrency 1 \

  --min-instances 0 \
  --max-instances 3 \
  --allow-unauthenticated \
  $ENV_FLAGS

# 4. Fetch public service URL
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" --platform managed --region "$REGION" --format 'value(status.url)')

echo "=========================================================="
echo " Deployment Complete!"
echo " Service Public URL: $SERVICE_URL"
echo " Health Check: $SERVICE_URL/health"
echo " Interactive Docs: $SERVICE_URL/docs"
echo "=========================================================="
