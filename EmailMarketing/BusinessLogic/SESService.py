import boto3
from django.conf import settings

class SESService:
    @staticmethod
    def is_dummy():
        key_id = getattr(settings, "AWS_ACCESS_KEY_ID")
        return not key_id or "dummy" in key_id.lower() or key_id == ""

    @classmethod
    def get_client(cls):
        if cls.is_dummy():
            return None
        return boto3.client(
            'ses',
            region_name=getattr(settings, 'AWS_SES_REGION_NAME', 'us-east-1'),
            aws_access_key_id=getattr(settings, 'AWS_ACCESS_KEY_ID', None),
            aws_secret_access_key=getattr(settings, 'AWS_SECRET_ACCESS_KEY', None)
        )

    @classmethod
    def register_domain(cls, domain):
        if cls.is_dummy():
            # Return realistic mock SES DKIM CNAME records for local testing
            dns_records = {
                "dkim1": {
                    "type": "CNAME",
                    "host": f"token1._domainkey.{domain}",
                    "data": "token1.dkim.amazonses.com",
                    "valid": False
                },
                "dkim2": {
                    "type": "CNAME",
                    "host": f"token2._domainkey.{domain}",
                    "data": "token2.dkim.amazonses.com",
                    "valid": False
                },
                "dkim3": {
                    "type": "CNAME",
                    "host": f"token3._domainkey.{domain}",
                    "data": "token3.dkim.amazonses.com",
                    "valid": False
                }
            }
            return {
                "id": domain,
                "dns": dns_records
            }

        try:
            client = cls.get_client()
            
            # Start Easy DKIM verification process
            dkim_response = client.verify_domain_dkim(Domain=domain)
            tokens = dkim_response.get("DkimTokens", [])
            
            # Also request basic domain verification TXT record to verify domain identity
            client.verify_domain_identity(Domain=domain)

            dns_records = {}
            for idx, token in enumerate(tokens, 1):
                dns_records[f"dkim{idx}"] = {
                    "type": "CNAME",
                    "host": f"{token}._domainkey.{domain}",
                    "data": f"{token}.dkim.amazonses.com",
                    "valid": False
                }
            
            return {
                "id": domain,
                "dns": dns_records
            }
        except Exception as e:
            raise Exception(f"Failed to register domain with AWS SES: {str(e)}")

    @classmethod
    def validate_domain(cls, domain_name):
        if cls.is_dummy():
            return True # Auto-verify in local mock mode

        try:
            client = cls.get_client()
            
            # Get general verification status
            response = client.get_identity_verification_attributes(Identities=[domain_name])
            attrs = response.get("VerificationAttributes", {})
            status = attrs.get(domain_name, {}).get("VerificationStatus", "Pending")
            
            # Get DKIM verification status
            dkim_response = client.get_identity_dkim_attributes(Identities=[domain_name])
            dkim_attrs = dkim_response.get("DkimAttributes", {})
            dkim_status = dkim_attrs.get(domain_name, {}).get("DkimVerificationStatus", "Pending")

            return status == "Success" or dkim_status == "Success"
        except Exception as e:
            print(f"Failed to validate domain {domain_name} with AWS SES: {e}")
            return False
