import re


class TemplateRenderer:
    VARIABLE_PATTERN = re.compile(r"\{\{\s*([^}]+)\s*\}\}")

    @classmethod
    def extract_variables(cls, content):
        if not content:
            return []
        matches = cls.VARIABLE_PATTERN.findall(content)
        variables = set()
        for match in matches:
            parts = match.strip().split("|")
            var_name = parts[0].strip()
            if var_name.startswith("recipient."):
                var_name = var_name.split(".", 1)[1]
            
            var_name = var_name.strip()
            exclude_vars = {
                "unsubscribe_url", "unsubscribe_link", "brand_color",
                "store_name", "shop_name", "store.name", "shop.name"
            }
            if var_name and var_name.lower() not in exclude_vars:
                variables.add(var_name)
        return list(sorted(variables))

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
        store_title = ""
        if contact and hasattr(contact, "store") and contact.store:
            store_title = contact.store.name or ""
        elif brand_settings:
            store_title = brand_settings.store_name or ""

        context = {
            "first_name": contact.first_name or "",
            "last_name": contact.last_name or "",
            "email": contact.email or "",
            "phone": contact.phone or "",
            "city": contact.city or "",
            "country": contact.country or "",
            "store_name": store_title,
            "shop_name": store_title,
            "store.name": store_title,
            "shop.name": store_title,
            "brand_color": "#3B82F6",
        }
        if brand_settings:
            context["brand_color"] = brand_settings.brand_color or "#3B82F6"
            if not context["store_name"] and brand_settings.store_name:
                context["store_name"] = brand_settings.store_name
                context["shop_name"] = brand_settings.store_name
                context["store.name"] = brand_settings.store_name
                context["shop.name"] = brand_settings.store_name
        if extra:
            context.update(extra)
        return context

    @classmethod
    def inject_preview_text(cls, html_content, preview_text):
        if not html_content:
            html_content = ""
            
        html_content = re.sub(
            r"""<div[^>]*style=["'][^"']*display:\s*none;\s*max-height:\s*0px;\s*overflow:\s*hidden;[^"']*["'][^>]*>.*?</div>""",
            "",
            html_content,
            flags=re.IGNORECASE | re.DOTALL
        )
        
        if not preview_text:
            return html_content
            
        padding = "&zwnj;&nbsp;" * 100
        hidden_div = f'<div style="display: none; max-height: 0px; overflow: hidden; mso-hide: all; opacity: 0; visibility: hidden;">{preview_text}{padding}</div>'
        
        body_match = re.search(r"<body[^>]*>", html_content, re.IGNORECASE)
        if body_match:
            insert_pos = body_match.end()
            return html_content[:insert_pos] + hidden_div + html_content[insert_pos:]
        
        return hidden_div + html_content
