"""
Design settings routes - manage design tokens via web UI.
"""
import re
import json
import logging
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from typing import Dict, Any

logger = logging.getLogger(__name__)
router = APIRouter()

# Templates
templates = Jinja2Templates(directory="web/templates")

# Path to design tokens CSS
DESIGN_TOKENS_PATH = Path(__file__).parent.parent / "static" / "design-tokens.css"
BRANDING_PATH = Path(__file__).parent.parent / "static" / "branding.json"

# Default branding values
DEFAULT_BRANDING = {
    "icon": "lightning",
    "customIconUrl": "",
    "appName": "Job Notification",
    "tagline": "Bot Dashboard",
    "gradientFrom": "#8b5cf6",
    "gradientTo": "#9333ea"
}

def get_branding() -> dict:
    """Load branding from JSON file or return defaults"""
    try:
        if BRANDING_PATH.exists():
            with open(BRANDING_PATH) as f:
                return {**DEFAULT_BRANDING, **json.load(f)}
    except Exception:
        pass
    return DEFAULT_BRANDING

# Add branding as a global template variable
templates.env.globals["branding"] = get_branding


class DesignTokensUpdate(BaseModel):
    """Request body for updating design tokens."""
    tokens: Dict[str, str]


class BrandingUpdate(BaseModel):
    """Request body for updating branding."""
    icon: str = "lightning"
    customIconUrl: str = ""
    appName: str = "Job Notification"
    tagline: str = "Bot Dashboard"
    gradientFrom: str = "#8b5cf6"
    gradientTo: str = "#9333ea"


def parse_css_variables(css_content: str) -> Dict[str, Any]:
    """
    Parse CSS file and extract variables with their values and comments.

    Returns dict with structure:
    {
        "sections": [
            {
                "name": "BRAND COLORS",
                "variables": [
                    {"name": "--color-accent", "value": "#8b5cf6", "comment": "Violet - primary brand color"}
                ]
            }
        ]
    }
    """
    sections = []
    current_section = None

    # Pattern for section headers like /* ========== BRAND COLORS ========== */
    section_pattern = re.compile(r'/\*\s*=+\s*(.+?)\s*=+\s*\*/')

    # Pattern for CSS variable with optional comment
    # Matches: --var-name: value;  /* comment */
    # Or: --var-name: value;
    var_pattern = re.compile(r'(--[\w-]+):\s*([^;]+);\s*(?:/\*\s*(.+?)\s*\*/)?')

    lines = css_content.split('\n')
    in_root = False

    for line in lines:
        stripped = line.strip()

        # Check for :root start
        if ':root' in stripped:
            in_root = True
            continue

        # Check for closing brace (end of :root)
        if in_root and stripped == '}':
            in_root = False
            continue

        if not in_root:
            continue

        # Check for section header
        section_match = section_pattern.search(line)
        if section_match:
            section_name = section_match.group(1).strip()
            current_section = {"name": section_name, "variables": []}
            sections.append(current_section)
            continue

        # Check for variable definition
        var_match = var_pattern.search(line)
        if var_match and current_section is not None:
            var_name = var_match.group(1)
            var_value = var_match.group(2).strip()
            var_comment = var_match.group(3) or ""

            # Determine variable type based on value
            var_type = "text"
            if var_value.startswith('#') or var_value.startswith('rgb') or var_value.startswith('hsl'):
                var_type = "color"
            elif var_name.startswith('--font-'):
                var_type = "font"
            elif re.search(r'\d(rem|px|em|%)(\s|$|;)', var_value):
                # Match actual CSS units (e.g., "0.5rem", "10px") not substrings like "system"
                var_type = "size"
            elif re.search(r'\dms(\s|$|;)', var_value):
                var_type = "duration"

            current_section["variables"].append({
                "name": var_name,
                "value": var_value,
                "comment": var_comment,
                "type": var_type
            })

    return {"sections": sections}


def update_css_variables(css_content: str, updates: Dict[str, str]) -> str:
    """
    Update CSS variable values in the content.

    Args:
        css_content: Original CSS content
        updates: Dict of {variable_name: new_value}

    Returns:
        Updated CSS content
    """
    result = css_content

    for var_name, new_value in updates.items():
        # Pattern to match the variable and preserve the comment
        # Use [^;]* (zero or more) to handle empty values
        pattern = re.compile(
            rf'({re.escape(var_name)}):\s*[^;]*;(\s*/\*[^*]*\*/)?',
            re.MULTILINE
        )

        def replacer(match):
            comment = match.group(2) or ""
            return f"{var_name}: {new_value};{comment}"

        result = pattern.sub(replacer, result)

    return result


@router.get("/design", response_class=HTMLResponse)
async def design_settings_page(request: Request):
    """Render design settings page."""
    return templates.TemplateResponse("design_settings_new.html", {"request": request})


@router.get("/api/design/tokens")
async def get_design_tokens():
    """Get all design tokens from CSS file."""
    try:
        if not DESIGN_TOKENS_PATH.exists():
            return {"success": False, "error": "Design tokens file not found"}

        css_content = DESIGN_TOKENS_PATH.read_text()
        tokens = parse_css_variables(css_content)

        return {"success": True, **tokens}

    except Exception as e:
        logger.error(f"Error reading design tokens: {e}")
        return {"success": False, "error": str(e)}


@router.put("/api/design/tokens")
async def update_design_tokens(request: DesignTokensUpdate):
    """Update design tokens in CSS file."""
    try:
        if not DESIGN_TOKENS_PATH.exists():
            return {"success": False, "error": "Design tokens file not found"}

        # Log what we're receiving
        for var_name, value in request.tokens.items():
            logger.info(f"Updating token {var_name} = {repr(value)}")

        css_content = DESIGN_TOKENS_PATH.read_text()
        updated_content = update_css_variables(css_content, request.tokens)

        # Write back to file
        DESIGN_TOKENS_PATH.write_text(updated_content)

        logger.info(f"Updated {len(request.tokens)} design tokens")
        return {"success": True, "updated": len(request.tokens)}

    except Exception as e:
        logger.error(f"Error updating design tokens: {e}")
        return {"success": False, "error": str(e)}


@router.post("/api/design/reset")
async def reset_design_tokens():
    """Reset design tokens to defaults (from git)."""
    try:
        import subprocess
        result = subprocess.run(
            ["git", "checkout", "HEAD", "--", str(DESIGN_TOKENS_PATH)],
            capture_output=True,
            text=True,
            cwd=DESIGN_TOKENS_PATH.parent.parent.parent
        )

        if result.returncode != 0:
            return {"success": False, "error": result.stderr}

        return {"success": True}

    except Exception as e:
        logger.error(f"Error resetting design tokens: {e}")
        return {"success": False, "error": str(e)}


# ==================== Branding API ====================

@router.get("/api/design/branding")
async def get_branding():
    """Get branding settings."""
    import json
    try:
        if not BRANDING_PATH.exists():
            # Return defaults
            return {
                "success": True,
                "branding": {
                    "icon": "lightning",
                    "customIconUrl": "",
                    "appName": "Job Notification",
                    "tagline": "Bot Dashboard",
                    "gradientFrom": "#8b5cf6",
                    "gradientTo": "#9333ea"
                }
            }

        branding = json.loads(BRANDING_PATH.read_text())
        return {"success": True, "branding": branding}

    except Exception as e:
        logger.error(f"Error reading branding: {e}")
        return {"success": False, "error": str(e)}


@router.put("/api/design/branding")
async def update_branding(request: BrandingUpdate):
    """Update branding settings and sync gradient colors to CSS tokens."""
    import json
    try:
        branding = request.dict()
        BRANDING_PATH.write_text(json.dumps(branding, indent=4))

        # Also sync gradient colors to CSS design tokens so buttons use the same colors
        if DESIGN_TOKENS_PATH.exists():
            css_content = DESIGN_TOKENS_PATH.read_text()
            gradient_updates = {
                "--color-gradient-from": request.gradientFrom,
                "--color-gradient-to": request.gradientTo
            }
            updated_css = update_css_variables(css_content, gradient_updates)
            DESIGN_TOKENS_PATH.write_text(updated_css)
            logger.info(f"Synced gradient colors to design tokens")

        logger.info(f"Updated branding: icon={request.icon}, appName={request.appName}")
        return {"success": True}

    except Exception as e:
        logger.error(f"Error updating branding: {e}")
        return {"success": False, "error": str(e)}
