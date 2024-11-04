import json
import logging
import requests
import hashlib
import hmac
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from django.utils import timezone
from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Payment
from rest_framework_api_key.permissions import HasAPIKey
from rest_framework.permissions import AllowAny
from .serializers import PaymentSerializer
from django.views.decorators.http import require_POST

logger = logging.getLogger(__name__)

class InitiatePaymentView(APIView):
    permission_classes = [HasAPIKey]

    def post(self, request):
        serializer = PaymentSerializer(data=request.data)

        # Validate incoming data
        if not serializer.is_valid():
            return Response({
                "error": "Validation Error",
                "message": serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        amount = data.get("amount")
        currency = data.get("currency", "UGX")
        name = data.get("name", "Anonymous")
        email = data.get("email")
        phone_number = data.get("phone_number")
        payment_method = data.get("payment_method")
        project = data.get("project")

        # Generate unique transaction reference
        tx_ref = f"tx-{name}-{timezone.now().timestamp()}"

        base_url = 'https://tak-payments-api.up.railway.app'  # Update with your base URL
        url = f"{settings.FLUTTERWAVE_BASE_URL}/payments"

        headers = {
            "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
            "Content-Type": "application/json"
        }

        payment_data = {
            "tx_ref": tx_ref,
            "amount": str(amount),
            "currency": currency,
            "redirect_url": f"{base_url}/api/payment/verify/",
            "customer": {
                "email": email,
                "phonenumber": phone_number,
                "name": name
            },
            "customizations": {
                "title": 'TAK Poultry Farm'
            }
        }

        logger.info(f"Sending payment data: {payment_data}")

        try:
            response = requests.post(url, json=payment_data, headers=headers)
            logger.info(f"Flutterwave Response Status: {response.status_code}")
            logger.info(f"Flutterwave Response Body: {response.text}")

            if response.status_code == 200:
                # Save payment initiation response to log or database
                Payment.objects.create(
                    name=name,
                    email=email,
                    phone_number=phone_number,
                    payment_method=payment_method,
                    project=project,
                    amount=amount,
                    currency=currency,
                    transaction_id=tx_ref,
                    flutterwave_response=json.dumps(response.json())  # Save response
                )
                return Response(response.json(), status=status.HTTP_200_OK)
            else:
                logger.error(f"Payment initiation failed: {response.text}")
                return Response({
                    "error": "Payment Initiation Failed",
                    "details": response.text
                }, status=response.status_code)
        except requests.RequestException as req_error:
            logger.error(f"Request to Flutterwave failed: {str(req_error)}")
            return Response({
                "error": "Network Error",
                "message": str(req_error)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logger.error(f"Unexpected error in payment initiation: {str(e)}")
            return Response({
                "error": "Server Error",
                "message": str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class VerifyPaymentView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        # Log incoming request parameters
        logger.info(f"Verification request parameters: {request.GET}")
        
        # Get parameters from URL
        tx_ref = request.GET.get("tx_ref")
        transaction_id = request.GET.get("transaction_id")
        
        if not tx_ref:
            logger.error("Missing tx_ref parameter")
            return Response({
                "status": "failed",
                "message": "Missing transaction reference"
            }, status=status.HTTP_400_BAD_REQUEST)

        # First check our database
        try:
            payment = Payment.objects.get(transaction_id=tx_ref)
            
            # If payment is already marked as successful, return success
            if payment.status == "successful":
                logger.info(f"Payment {tx_ref} already verified as successful")
                return Response({
                    "status": "success",
                    "message": "Payment was successful",
                    "data": {
                        "amount": payment.amount,
                        "currency": payment.currency,
                        "name": payment.name,
                        "email": payment.email
                    }
                }, status=status.HTTP_200_OK)

        except Payment.DoesNotExist:
            logger.error(f"Payment not found for tx_ref: {tx_ref}")
            return Response({
                "status": "failed",
                "message": "Transaction not found"
            }, status=status.HTTP_404_NOT_FOUND)

        # Verify with Flutterwave
        try:
            # If transaction_id is provided, use it for verification
            if transaction_id:
                verify_url = f"{settings.FLUTTERWAVE_BASE_URL}/transactions/{transaction_id}/verify"
            else:
                # Fall back to using tx_ref
                verify_url = f"{settings.FLUTTERWAVE_BASE_URL}/transactions/verify_by_reference?tx_ref={tx_ref}"

            headers = {
                "Authorization": f"Bearer {settings.FLUTTERWAVE_SECRET_KEY}",
                "Content-Type": "application/json"
            }

            logger.info(f"Sending verification request to: {verify_url}")
            response = requests.get(verify_url, headers=headers)
            
            # Log the full response for debugging
            logger.info(f"Flutterwave verification response: {response.text}")

            # Check if the response is valid JSON
            try:
                data = response.json()
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON response from Flutterwave: {response.text}")
                return Response({
                    "status": "failed",
                    "message": "Invalid response from payment provider"
                }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

            # Check the response status
            if response.status_code == 200:
                # Extract status from the correct location in response
                payment_status = data.get("status", "")
                transaction_status = data.get("data", {}).get("status", "")

                if payment_status == "success" and transaction_status == "successful":
                    # Update payment record
                    payment.status = "successful"
                    payment.flutterwave_response = json.dumps(data)
                    payment.save()

                    logger.info(f"Payment {tx_ref} verified as successful")
                    return Response({
                        "status": "success",
                        "message": "Payment was successful",
                        "data": {
                            "amount": payment.amount,
                            "currency": payment.currency,
                            "name": payment.name,
                            "email": payment.email
                        }
                    }, status=status.HTTP_200_OK)
                else:
                    logger.warning(f"Payment {tx_ref} verification failed. Status: {payment_status}, Transaction status: {transaction_status}")
                    return Response({
                        "status": "failed",
                        "message": "Payment verification failed",
                        "details": data.get("message", "Unknown error")
                    }, status=status.HTTP_400_BAD_REQUEST)
            else:
                logger.error(f"Flutterwave API error: {response.status_code} - {response.text}")
                return Response({
                    "status": "failed",
                    "message": "Error verifying payment",
                    "details": data.get("message", "Unknown error")
                }, status=status.HTTP_400_BAD_REQUEST)

        except requests.RequestException as e:
            logger.error(f"Network error during verification: {str(e)}")
            return Response({
                "status": "failed",
                "message": "Network error during verification"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as e:
            logger.error(f"Unexpected error during verification: {str(e)}")
            return Response({
                "status": "failed",
                "message": "Internal server error"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@csrf_exempt
# @require_POST
def flutterwave_webhook(request):
  
    logger.info("Received webhook request")
    logger.info(f"Request Headers: {dict(request.headers)}")
    logger.info(f"Request Body: {request.body.decode('utf-8')}")

    # Get the verification hash from headers
    received_hash = request.headers.get("verif-hash")
    
    if not received_hash:
        logger.error("No verification hash in request headers")
        return JsonResponse({
            "status": "failed",
            "message": "Missing verification hash"
        }, status=400)

    if received_hash != settings.FLUTTERWAVE_SECRET_HASH:
        logger.error(f"Hash verification failed. Received: {received_hash}")
        return JsonResponse({
            "status": "failed",
            "message": "Invalid verification hash"
        }, status=403)

    try:
        # Parse the webhook data
        webhook_data = json.loads(request.body.decode('utf-8'))
        logger.info(f"Parsed webhook data: {json.dumps(webhook_data, indent=2)}")

        tx_ref = webhook_data.get("txRef")  # Note: txRef instead of tx_ref
        status = webhook_data.get("status")
        amount = webhook_data.get("amount")
        currency = webhook_data.get("currency")
        customer = webhook_data.get("customer", {})
        
        logger.info(f"Processing webhook: txRef={tx_ref}, status={status}, amount={amount} {currency}")

        if not tx_ref:
            logger.error("No txRef found in webhook data")
            return JsonResponse({
                "status": "failed",
                "message": "Missing transaction reference"
            }, status=400)

        
        if status == "successful":
            try:
                # Find payment by transaction_id (which stores txRef)
                payment = Payment.objects.get(transaction_id=tx_ref)
                
                # Update payment details
                payment.status = "successful"
                payment.flutterwave_response = json.dumps(webhook_data)
                
                # Update customer details if needed
                if customer:
                    payment.name = customer.get("fullName", payment.name)
                    payment.email = customer.get("email", payment.email)
                    payment.phone_number = customer.get("phone", payment.phone_number)
                
                payment.save()
                
                logger.info(f"Successfully updated payment status for txRef: {tx_ref}")
                
                return JsonResponse({
                    "status": "success",
                    "message": "Payment processed successfully"
                }, status=200)
                
            except Payment.DoesNotExist:
                logger.error(f"Payment not found for txRef: {tx_ref}")
                return JsonResponse({
                    "status": "failed",
                    "message": f"Transaction not found: {tx_ref}"
                }, status=404)
            except Exception as e:
                logger.error(f"Error updating payment: {str(e)}")
                return JsonResponse({
                    "status": "failed",
                    "message": "Error updating payment status"
                }, status=500)
        else:
            logger.info(f"Payment status not successful: {status}")
            return JsonResponse({
                "status": "success",  # Still return success to acknowledge receipt
                "message": f"Payment status: {status}"
            }, status=200)

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse webhook data: {str(e)}")
        return JsonResponse({
            "status": "failed",
            "message": "Invalid JSON payload"
        }, status=400)
    except Exception as e:
        logger.error(f"Unexpected error processing webhook: {str(e)}")
        return JsonResponse({
            "status": "failed",
            "message": "Internal server error"
        }, status=500)