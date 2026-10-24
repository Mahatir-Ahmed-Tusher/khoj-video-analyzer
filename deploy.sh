#!/usr/bin/env bash
# ==============================================================================
# Khoj Video Analyzer - 1-Click Deployment Script for Google Cloud Shell
# ==============================================================================
set -e

SERVICE_NAME="khoj-video-analyzer"
REGION="us-central1" # Recommended region with 100% Always-Free tier coverage

echo "=========================================================="
echo " Starting Deployment for: $SERVICE_NAME"
echo " Target Region: $REGION"
echo "=========================================================="

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
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --concurrency 10 \
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
