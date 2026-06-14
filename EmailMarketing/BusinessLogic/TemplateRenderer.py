import re


class TemplateRenderer:
    VARIABLE_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")

    @classmethod
    def render(cls, content, context):
        if not content:
            return ""

        def replace(match):
            raw_expr = match.group(1).strip()
            parts = raw_expr.split("|")
            var_name = parts[0].strip()

            if var_name.startswith("recipient."):
                var_name = var_name.split(".", 1)[1]

            value = context.get(var_name)

            if (value is None or value == "") and len(parts) > 1:
                filter_part = parts[1].strip()
                if filter_part.startswith("default:"):
                    default_val = filter_part.split(":", 1)[1].strip()
                    if default_val.startswith('"') and default_val.endswith('"'):
                        default_val = default_val[1:-1]
                    elif default_val.startswith("'") and default_val.endswith("'"):
                        default_val = default_val[1:-1]
                    return default_val

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
