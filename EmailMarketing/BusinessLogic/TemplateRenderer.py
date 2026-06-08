import re


class TemplateRenderer:
    VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

    @classmethod
    def render(cls, content, context):
        if not content:
            return ""

        def replace(match):
            key = match.group(1)
            value = context.get(key, "")
            return str(value) if value is not None else ""

        return cls.VARIABLE_PATTERN.sub(replace, content)

    @classmethod
    def build_contact_context(cls, contact, brand_settings=None, extra=None):
        context = {
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "email": contact.email or "",
            "phone": contact.phone or "",
            "store_name": "",
            "brand_color": "#3B82F6",
        }
        if brand_settings:
            context["store_name"] = brand_settings.store_name or ""
            context["brand_color"] = brand_settings.brand_color or "#3B82F6"
        if extra:
            context.update(extra)
        return context
