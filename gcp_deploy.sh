#!/bin/bash

gcloud run deploy tax-estimator \
  --source . \
  --region us-central1 \
  --allow-unauthenticated