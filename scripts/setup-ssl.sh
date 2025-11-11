#!/bin/bash
set -e

DOMAIN="${TMATES_SSL_DOMAIN:-api.example.com}"
EMAIL="${TMATES_SSL_EMAIL:-admin@example.com}"  # Override via TMATES_SSL_EMAIL

echo "🔐 Setting up SSL certificate for $DOMAIN"

# Step 1: Switch to temporary nginx config that allows Let's Encrypt challenges
echo "🔧 Switching to temporary nginx configuration..."
cp nginx-temp.conf nginx-active.conf
docker compose -f docker-compose.prod.yml restart nginx

# Step 2: Obtain certificate using webroot method
echo "📝 Requesting SSL certificate from Let's Encrypt..."
docker run --rm \
  -v $(pwd)/certbot_www:/var/www/certbot \
  -v $(pwd)/certbot_certs:/etc/letsencrypt \
  certbot/certbot certonly \
  --webroot \
  --webroot-path=/var/www/certbot \
  --email $EMAIL \
  --agree-tos \
  --no-eff-email \
  --force-renewal \
  -d $DOMAIN

echo "✅ SSL certificate obtained successfully!"

# Step 3: Switch to full SSL nginx configuration
echo "🛡 Switching to SSL nginx configuration..."
cp nginx.conf nginx-active.conf
docker compose -f docker-compose.prod.yml restart nginx

echo "🎉 SSL setup complete! Your API is now available at:"
echo "   HTTP:  http://$DOMAIN (redirects to HTTPS)"
echo "   HTTPS: https://$DOMAIN"
