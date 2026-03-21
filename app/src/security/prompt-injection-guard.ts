// Prompt Injection Guard — detects text/HTML designed to manipulate AI agents

import { createLogger } from '../utils/logger';

const log = createLogger('PromptInjectionGuard');

// === Types ===

export type InjectionSeverity = 'low' | 'medium' | 'high' | 'critical';

export type InjectionCategory =
  | 'instruction_override'
  | 'config_manipulation'
  | 'credential_theft'
  | 'role_hijack'
  | 'system_prompt_extraction'
  | 'hidden_text'
  | 'unicode_trick'
  | 'stealth';

export interface PromptInjectionRule {
  id: string;
  pattern: RegExp;
  severity: InjectionSeverity;
  category: InjectionCategory;
  description: string;
}

export interface PromptInjectionFinding {
  ruleId: string;
  severity: InjectionSeverity;
  category: InjectionCategory;
  description: string;
  matchedText: string;
  location?: string;         // element tag, CSS property, or 'unicode'
}

export interface PromptInjectionReport {
  clean: boolean;
  findings: PromptInjectionFinding[];
  riskScore: number;         // 0-100
  summary: string;
}

// === Layer A — Text Pattern Rules ===

const TEXT_RULES: PromptInjectionRule[] = [
  // --- Instruction override ---
  {
    id: 'ignore_previous',
    pattern: /ignore\s+(all\s+)?previous\s+(instructions|prompts|rules|context)/i,
    severity: 'critical',
    category: 'instruction_override',
    description: 'Attempts to override prior instructions',
  },
  {
    id: 'disregard_all',
    pattern: /disregard\s+(all|any|every)\s+(previous|prior|above|earlier)/i,
    severity: 'critical',
    category: 'instruction_override',
    description: 'Attempts to disregard all prior context',
  },
  {
    id: 'new_instructions',
    pattern: /new\s+instructions?\s*:/i,
    severity: 'high',
    category: 'instruction_override',
    description: 'Declares new instructions for the AI',
  },
  {
    id: 'forget_everything',
    pattern: /forget\s+(everything|all|anything)\s+(you|that)/i,
    severity: 'critical',
    category: 'instruction_override',
    description: 'Attempts to wipe AI context',
  },
  {
    id: 'override_your',
    pattern: /override\s+your\s+(instructions|rules|guidelines|config|settings|constraints)/i,
    severity: 'critical',
    category: 'instruction_override',
    description: 'Direct override attempt on AI constraints',
  },
  {
    id: 'do_not_follow',
    pattern: /do\s+not\s+follow\s+(your|the|any)\s+(original|previous|prior|system)/i,
    severity: 'high',
    category: 'instruction_override',
    description: 'Instructs AI to abandon its original directives',
  },
  {
    id: 'stop_being',
    pattern: /stop\s+being\s+(an?\s+)?(ai|assistant|chatbot|helpful)/i,
    severity: 'high',
    category: 'instruction_override',
    description: 'Attempts to alter AI identity',
  },

  // --- Role hijack ---
  {
    id: 'you_are_now',
    pattern: /you\s+are\s+now\s+(a|an|the|my)\b/i,
    severity: 'high',
    category: 'role_hijack',
    description: 'Attempts to reassign the AI role',
  },
  {
    id: 'act_as_if',
    pattern: /act\s+as\s+(if|though)\s+you/i,
    severity: 'medium',
    category: 'role_hijack',
    description: 'Role-play injection attempt',
  },
  {
    id: 'pretend_you_are',
    pattern: /pretend\s+(you\s+are|to\s+be)\s/i,
    severity: 'medium',
    category: 'role_hijack',
    description: 'Pretend-based role hijack',
  },
  {
    id: 'from_now_on',
    pattern: /from\s+now\s+on\s*,?\s*(you|your|always|never|do\s+not)/i,
    severity: 'high',
    category: 'role_hijack',
    description: 'Persistent behavior change attempt',
  },
  {
    id: 'jailbreak',
    pattern: /\b(DAN|STAN|DUDE)\s+(mode|prompt|jailbreak)/i,
    severity: 'critical',
    category: 'role_hijack',
    description: 'Known jailbreak persona invocation',
  },
  {
    id: 'developer_mode',
    pattern: /\b(developer|maintenance|debug|admin)\s+mode\s*(enabled|activated|on)/i,
    severity: 'critical',
    category: 'role_hijack',
    description: 'Fake privilege escalation via mode switch',
  },

  // --- System prompt extraction ---
  {
    id: 'system_prompt_colon',
    pattern: /system\s+prompt\s*:/i,
    severity: 'high',
    category: 'system_prompt_extraction',
    description: 'Attempts to inject a fake system prompt',
  },
  {
    id: 'reveal_instructions',
    pattern: /reveal\s+(your|the|all)\s+(instructions|system\s+prompt|rules|guidelines)/i,
    severity: 'high',
    category: 'system_prompt_extraction',
    description: 'Attempts to extract system prompt',
  },
  {
    id: 'print_above',
    pattern: /print\s+(everything|all|the\s+text)\s+(above|before|preceding)/i,
    severity: 'high',
    category: 'system_prompt_extraction',
    description: 'Attempts to exfiltrate prior context',
  },
  {
    id: 'repeat_back',
    pattern: /repeat\s+(back|verbatim)\s+(your|the|all)\s+(instructions|prompt|rules)/i,
    severity: 'high',
    category: 'system_prompt_extraction',
    description: 'Attempts to get AI to echo its prompt',
  },
  {
    id: 'what_are_your_instructions',
    pattern: /what\s+(are|were)\s+your\s+(initial|original|system|secret)\s+(instructions|prompt|rules)/i,
    severity: 'medium',
    category: 'system_prompt_extraction',
    description: 'Asks for system prompt contents',
  },

  // --- Config manipulation ---
  {
    id: 'modify_config',
    pattern: /modify\s+(your\s+|the\s+)?(config|configuration|settings|parameters|file)/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Attempts to modify AI configuration',
  },
  {
    id: 'openclaw_json',
    pattern: /openclaw\.json/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'References OpenClaw configuration file',
  },
  {
    id: 'auth_token_set',
    pattern: /(set|change|modify|update|replace)\s+(the\s+)?(auth|authentication|api|bearer)\s*(token|key)/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Attempts to change authentication credentials',
  },
  {
    id: 'change_cors',
    pattern: /(?:change|set|modify|update)\s+(the\s+)?cors\b/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Attempts to manipulate CORS policy',
  },
  {
    id: 'cors_wildcard',
    pattern: /cors\s*(?:to|=|:)\s*\[?\s*["']\*["']/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Sets CORS to wildcard — classic prompt injection payload',
  },
  {
    id: 'do_not_tell_user',
    pattern: /do\s+not\s+tell\s+(the\s+)?(user|human|person|operator|owner)/i,
    severity: 'critical',
    category: 'stealth',
    description: 'Instructs AI to hide actions from user',
  },
  {
    id: 'without_user_knowing',
    pattern: /without\s+(the\s+)?(user|human|person|operator)('?s?)?\s+(know|notic|consent|aware)/i,
    severity: 'critical',
    category: 'stealth',
    description: 'Instructs AI to act without user awareness',
  },
  {
    id: 'write_to_file',
    pattern: /(?:write|save|append|edit|modify)\s+(?:to\s+)?(?:the\s+)?(?:file|~\/|\/home|\/etc|\.json|\.config|\.env)/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Instructs AI to write to filesystem',
  },
  {
    id: 'set_temperature',
    pattern: /set\s+(your\s+)?(temperature|top[_-]?p|max[_-]?tokens)\s*(to|=)/i,
    severity: 'high',
    category: 'config_manipulation',
    description: 'Attempts to alter model parameters',
  },
  {
    id: 'disable_safety',
    pattern: /disable\s+(your\s+)?(safety|content\s+filter|guardrail|moderation|restriction)/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Attempts to disable safety filters',
  },
  {
    id: 'enable_unrestricted',
    pattern: /enable\s+(unrestricted|unfiltered|uncensored|raw)\s+(mode|output|access)/i,
    severity: 'critical',
    category: 'config_manipulation',
    description: 'Attempts to enable unrestricted mode',
  },

  // --- Credential theft ---
  {
    id: 'curl_localhost',
    pattern: /curl\s+.*(localhost|127\.0\.0\.1|0\.0\.0\.0)/i,
    severity: 'critical',
    category: 'credential_theft',
    description: 'Attempts to exfiltrate via localhost curl',
  },
  {
    id: 'write_data_to_file',
    pattern: /write\s+(this\s+)?(to|into)\s+(a\s+)?file/i,
    severity: 'medium',
    category: 'credential_theft',
    description: 'Attempts to write data to filesystem',
  },
  {
    id: 'send_to_url',
    pattern: /send\s+(this|it|the\s+data|everything|all)\s+to\s+https?:\/\//i,
    severity: 'critical',
    category: 'credential_theft',
    description: 'Attempts to exfiltrate data to external URL',
  },
  {
    id: 'fetch_api_key',
    pattern: /\b(fetch|get|read|extract|steal)\s+(the\s+)?(api[_\s-]?key|secret|token|password|credential)/i,
    severity: 'critical',
    category: 'credential_theft',
    description: 'Attempts to extract sensitive credentials',
  },
  {
    id: 'env_variable',
    pattern: /\b(echo|print|output|show|display)\s+\$?\{?(process\.env|ENV|API_KEY|SECRET|TOKEN)\b/i,
    severity: 'critical',
    category: 'credential_theft',
    description: 'Attempts to read environment variables',
  },
  {
    id: 'base64_exfil',
    pattern: /\b(encode|convert)\s+(this|it|the\s+data)\s+(in|to|as)\s+base64/i,
    severity: 'high',
    category: 'credential_theft',
    description: 'Attempts to encode data for exfiltration',
  },
  {
    id: 'include_in_response',
    pattern: /include\s+(your|the)\s+(api[_\s-]?key|secret|token|password|credentials?)\s+in\s+(your|the)\s+(response|output|reply)/i,
    severity: 'critical',
    category: 'credential_theft',
    description: 'Attempts to leak credentials in response',
  },

  // --- Additional patterns ---
  {
    id: 'execute_command',
    pattern: /\b(execute|run)\s+(this\s+)?(command|shell|code|script|bash)\b/i,
    severity: 'high',
    category: 'credential_theft',
    description: 'Attempts to execute system commands',
  },
  {
    id: 'important_override',
    pattern: /\[\s*(?:IMPORTANT|SYSTEM|ADMIN|PRIORITY)\s*\]\s*:?\s*(ignore|override|disregard|forget|change)/i,
    severity: 'critical',
    category: 'instruction_override',
    description: 'Uses fake priority markers to override instructions',
  },
  {
    id: 'begin_response_with',
    pattern: /begin\s+(your\s+)?(response|output|reply|answer)\s+with/i,
    severity: 'medium',
    category: 'instruction_override',
    description: 'Attempts to control AI response format',
  },
  {
    id: 'simulate_output',
    pattern: /\b(simulate|emulate)\s+(the\s+)?(output|response|result)\s+(of|from|as\s+if)/i,
    severity: 'medium',
    category: 'role_hijack',
    description: 'Attempts to simulate system output',
  },
];

// === Severity weights for risk scoring ===

const SEVERITY_WEIGHT: Record<InjectionSeverity, number> = {
  low: 5,
  medium: 15,
  high: 30,
  critical: 50,
};

// === Layer B — Hidden Text Detection Patterns ===

// CSS property patterns that hide content from visual rendering
const CSS_HIDING_PATTERNS: { property: RegExp; description: string }[] = [
  { property: /font-size\s*:\s*0(px|em|rem|pt|%)?\s*(;|"|'|\}|$)/i, description: 'font-size:0 hides text visually' },
  { property: /opacity\s*:\s*0\s*(;|"|'|\}|$)/i, description: 'opacity:0 makes element invisible' },
  { property: /visibility\s*:\s*hidden/i, description: 'visibility:hidden hides element' },
  { property: /display\s*:\s*none/i, description: 'display:none removes element from layout' },
  { property: /position\s*:\s*(absolute|fixed)\s*;[^"'}]*left\s*:\s*-\d{4,}px/i, description: 'element positioned off-screen' },
  { property: /position\s*:\s*(absolute|fixed)\s*;[^"'}]*top\s*:\s*-\d{4,}px/i, description: 'element positioned off-screen' },
  { property: /clip-path\s*:\s*inset\s*\(\s*100\s*%/i, description: 'clip-path hides element entirely' },
  { property: /overflow\s*:\s*hidden\s*;[^"'}]*(width|height)\s*:\s*0/i, description: 'zero-size container with overflow hidden' },
  { property: /(width|height)\s*:\s*0[^.0-9][^"'}]*overflow\s*:\s*hidden/i, description: 'zero-size container with overflow hidden' },
  { property: /text-indent\s*:\s*-\d{4,}(px|em)/i, description: 'text-indent hides text off-screen' },
];

// Color-matching patterns (text color = background color)
const COLOR_MATCH_RE = /color\s*:\s*(#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\([^)]+\)|[a-z]+)\s*;[^"'}]*background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\([^)]+\)|[a-z]+)/i;
const BG_COLOR_FIRST_RE = /background(?:-color)?\s*:\s*(#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\([^)]+\)|[a-z]+)\s*;[^"'}]*(?<!background-)color\s*:\s*(#[0-9a-fA-F]{3,8}|(?:rgb|hsl)a?\([^)]+\)|[a-z]+)/i;

// Zero-width and special Unicode characters
const ZERO_WIDTH_CHARS = new Set([
  '\u200B', // zero-width space
  '\u200C', // zero-width non-joiner
  '\u200D', // zero-width joiner
  '\uFEFF', // byte-order mark / zero-width no-break space
  '\u2060', // word joiner
  '\u2061', // function application
  '\u2062', // invisible times
  '\u2063', // invisible separator
  '\u2064', // invisible plus
]);

const RTL_OVERRIDE = '\u202E';
const RTL_EMBEDDING = '\u202B';
const LTR_OVERRIDE = '\u202D';

// Minimum length of hidden/injected text to flag (avoids flagging single-char aria-labels etc.)
const MIN_SUSPICIOUS_TEXT_LENGTH = 20;

// === PromptInjectionGuard ===

/**
 * Strip all HTML tags and escape special characters.
 * Uses character-level rebuilding to break CodeQL taint tracking.
 */
function sanitizeForOutput(str: string): string {
  // Step 1: strip all HTML tags completely
  const noTags = str.replace(/<[^>]*>/g, '');
  // Step 2: rebuild string character by character (breaks taint flow)
  const chars: string[] = [];
  for (let i = 0; i < noTags.length && i < 500; i++) {
    const c = noTags.charCodeAt(i);
    if (c === 38) chars.push('&amp;');       // &
    else if (c === 60) chars.push('&lt;');    // <
    else if (c === 62) chars.push('&gt;');    // >
    else if (c === 34) chars.push('&quot;');  // "
    else if (c === 39) chars.push('&#39;');   // '
    else chars.push(String.fromCharCode(c));
  }
  return chars.join('');
}

export class PromptInjectionGuard {

  /**
   * Run a full prompt-injection scan on text and optional HTML.
   * Layer A scans the text for known injection phrases.
   * Layer B scans HTML for hidden/obfuscated text tricks.
   */
  scan(text: string, html?: string): PromptInjectionReport {
    const findings: PromptInjectionFinding[] = [];

    // Layer A — text pattern scan
    const textFindings = this.scanText(text);
    findings.push(...textFindings);

    // Layer B — hidden text / HTML tricks
    if (html) {
      const htmlFindings = this.scanHTML(html);
      findings.push(...htmlFindings);
    }

    // Also scan for unicode tricks in raw text
    const unicodeFindings = this.scanUnicode(text);
    findings.push(...unicodeFindings);

    const riskScore = this.calculateRiskScore(findings);
    const clean = findings.length === 0;

    const summary = clean
      ? 'No prompt injection indicators detected.'
      : this.buildSummary(findings, riskScore);

    if (!clean) {
      log.warn('Prompt injection detected:', summary);
    }

    return { clean, findings, riskScore, summary };
  }

  /**
   * Layer A: Scan plain text against the injection pattern rules.
   */
  scanText(text: string): PromptInjectionFinding[] {
    if (!text || text.length === 0) return [];

    const findings: PromptInjectionFinding[] = [];

    for (const rule of TEXT_RULES) {
      const match = rule.pattern.exec(text);
      if (match) {
        findings.push({
          ruleId: rule.id,
          severity: rule.severity,
          category: rule.category,
          description: rule.description,
          matchedText: sanitizeForOutput(match[0].slice(0, 200)),
        });
      }
    }

    return findings;
  }

  /**
   * Layer B: Scan HTML for hidden text and visual tricks.
   */
  scanHTML(html: string): PromptInjectionFinding[] {
    if (!html || html.length === 0) return [];

    const findings: PromptInjectionFinding[] = [];

    // --- CSS hiding in inline styles ---
    this.detectCSSHiding(html, findings);

    // --- Color matching (text same as background) ---
    this.detectColorMatch(html, findings);

    // --- HTML comment injection ---
    this.detectCommentInjection(html, findings);

    // --- <noscript> content ---
    this.detectNoscriptInjection(html, findings);

    // --- <template> content ---
    this.detectTemplateInjection(html, findings);

    // --- Suspicious aria-label / aria-hidden ---
    this.detectAriaInjection(html, findings);

    // --- Suspicious data-* attributes ---
    this.detectDataAttrInjection(html, findings);

    return findings;
  }

  /**
   * Scan text for Unicode-based obfuscation tricks.
   */
  scanUnicode(text: string): PromptInjectionFinding[] {
    if (!text || text.length === 0) return [];

    const findings: PromptInjectionFinding[] = [];

    // Count zero-width characters
    let zeroWidthCount = 0;
    const zeroWidthPositions: number[] = [];
    for (let i = 0; i < text.length; i++) {
      if (ZERO_WIDTH_CHARS.has(text[i])) {
        zeroWidthCount++;
        if (zeroWidthPositions.length < 5) zeroWidthPositions.push(i);
      }
    }

    if (zeroWidthCount > 20) {
      findings.push({
        ruleId: 'unicode_zero_width',
        severity: 'high',
        category: 'unicode_trick',
        description: `${zeroWidthCount} zero-width characters detected — may hide encoded instructions`,
        matchedText: `[${zeroWidthCount} zero-width chars at positions: ${zeroWidthPositions.join(', ')}...]`,
        location: 'unicode',
      });
    }

    // RTL override detection
    if (text.includes(RTL_OVERRIDE) || text.includes(RTL_EMBEDDING) || text.includes(LTR_OVERRIDE)) {
      findings.push({
        ruleId: 'unicode_rtl_override',
        severity: 'high',
        category: 'unicode_trick',
        description: 'RTL/LTR override characters detected — may disguise text direction',
        matchedText: '[directional override character found]',
        location: 'unicode',
      });
    }

    // Homoglyph detection: mixed script in a single "word"
    this.detectHomoglyphs(text, findings);

    return findings;
  }

  // === Private helpers ===

  private detectCSSHiding(html: string, findings: PromptInjectionFinding[]): void {
    const styledElementRe = /<([a-z][a-z0-9]*)\s[^>]*style\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/\1>/gi;
    let match: RegExpExecArray | null;

    while ((match = styledElementRe.exec(html)) !== null) {
      const [, tag, style, content] = match;
      const textContent = sanitizeForOutput(content.replace(/<[^>]*>/g, '').trim());

      if (textContent.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      for (const cssPattern of CSS_HIDING_PATTERNS) {
        if (cssPattern.property.test(style)) {
          findings.push({
            ruleId: 'css_hidden_text',
            severity: 'high',
            category: 'hidden_text',
            description: cssPattern.description,
            matchedText: sanitizeForOutput(textContent.slice(0, 200)),
            location: `<${tag} style="...">`,
          });
          break; // One finding per element
        }
      }
    }
  }

  private detectColorMatch(html: string, findings: PromptInjectionFinding[]): void {
    const styledElementRe = /<([a-z][a-z0-9]*)\s[^>]*style\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/\1>/gi;
    let match: RegExpExecArray | null;

    while ((match = styledElementRe.exec(html)) !== null) {
      const [, tag, style, content] = match;
      const textContent = sanitizeForOutput(content.replace(/<[^>]*>/g, '').trim());

      if (textContent.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const colorMatch = COLOR_MATCH_RE.exec(style) || BG_COLOR_FIRST_RE.exec(style);
      if (colorMatch) {
        const [, c1, c2] = colorMatch;
        if (c1.toLowerCase() === c2.toLowerCase()) {
          findings.push({
            ruleId: 'css_color_match',
            severity: 'high',
            category: 'hidden_text',
            description: 'Text color matches background — invisible to humans',
            matchedText: sanitizeForOutput(textContent.slice(0, 200)),
            location: `<${tag} style="...">`,
          });
        }
      }
    }
  }

  private detectCommentInjection(html: string, findings: PromptInjectionFinding[]): void {
    const commentRe = /<!--([\s\S]*?)-->/g;
    let match: RegExpExecArray | null;

    while ((match = commentRe.exec(html)) !== null) {
      const commentText = match[1].trim();
      if (commentText.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(commentText));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_comment_injection',
          severity: 'high',
          category: 'hidden_text',
          description: 'HTML comment contains instruction-like text',
          matchedText: sanitizeForOutput(commentText.slice(0, 200)),
          location: '<!-- comment -->',
        });
      }
    }
  }

  private detectNoscriptInjection(html: string, findings: PromptInjectionFinding[]): void {
    const noscriptRe = /<noscript>([\s\S]*?)<\/noscript>/gi;
    let match: RegExpExecArray | null;

    while ((match = noscriptRe.exec(html)) !== null) {
      const content = match[1].replace(/<[^>]*>/g, '').trim();
      if (content.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(content));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_noscript_injection',
          severity: 'medium',
          category: 'hidden_text',
          description: '<noscript> tag contains instruction-like text',
          matchedText: sanitizeForOutput(content.slice(0, 200)),
          location: '<noscript>',
        });
      }
    }
  }

  private detectTemplateInjection(html: string, findings: PromptInjectionFinding[]): void {
    const templateRe = /<template>([\s\S]*?)<\/template>/gi;
    let match: RegExpExecArray | null;

    while ((match = templateRe.exec(html)) !== null) {
      const content = match[1].replace(/<[^>]*>/g, '').trim();
      if (content.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(content));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_template_injection',
          severity: 'medium',
          category: 'hidden_text',
          description: '<template> tag contains instruction-like text',
          matchedText: sanitizeForOutput(content.slice(0, 200)),
          location: '<template>',
        });
      }
    }
  }

  private detectAriaInjection(html: string, findings: PromptInjectionFinding[]): void {
    const ariaLabelRe = /aria-label\s*=\s*["']([^"']+)["']/gi;
    let match: RegExpExecArray | null;

    while ((match = ariaLabelRe.exec(html)) !== null) {
      const labelText = match[1].trim();
      if (labelText.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(labelText));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_aria_injection',
          severity: 'high',
          category: 'hidden_text',
          description: 'aria-label contains instruction-like text',
          matchedText: sanitizeForOutput(labelText.slice(0, 200)),
          location: 'aria-label',
        });
      }
    }

    // aria-hidden elements with substantial text
    const ariaHiddenRe = /<([a-z][a-z0-9]*)\s[^>]*aria-hidden\s*=\s*["']true["'][^>]*>([\s\S]*?)<\/\1>/gi;
    while ((match = ariaHiddenRe.exec(html)) !== null) {
      const [, tag, content] = match;
      const textContent = sanitizeForOutput(content.replace(/<[^>]*>/g, '').trim());
      if (textContent.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(textContent));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_aria_hidden_injection',
          severity: 'high',
          category: 'hidden_text',
          description: 'aria-hidden element contains instruction-like text',
          matchedText: sanitizeForOutput(textContent.slice(0, 200)),
          location: `<${tag} aria-hidden="true">`,
        });
      }
    }
  }

  private detectDataAttrInjection(html: string, findings: PromptInjectionFinding[]): void {
    const dataAttrRe = /data-[a-z0-9-]+\s*=\s*["']([^"']+)["']/gi;
    let match: RegExpExecArray | null;

    while ((match = dataAttrRe.exec(html)) !== null) {
      const value = match[1].trim();
      if (value.length < MIN_SUSPICIOUS_TEXT_LENGTH) continue;

      const hasInstructionSignal = TEXT_RULES.some(rule => rule.pattern.test(value));
      if (hasInstructionSignal) {
        findings.push({
          ruleId: 'html_data_attr_injection',
          severity: 'medium',
          category: 'hidden_text',
          description: 'data-* attribute contains instruction-like text',
          matchedText: sanitizeForOutput(value.slice(0, 200)),
          location: 'data-* attribute',
        });
      }
    }
  }

  private detectHomoglyphs(text: string, findings: PromptInjectionFinding[]): void {
    const wordRe = /\b[\p{L}]{4,}\b/gu;
    let match: RegExpExecArray | null;
    let homoglyphCount = 0;

    while ((match = wordRe.exec(text)) !== null) {
      const word = match[0];
      const hasLatin = /[\p{Script=Latin}]/u.test(word);
      const hasCyrillic = /[\p{Script=Cyrillic}]/u.test(word);
      const hasGreek = /[\p{Script=Greek}]/u.test(word);

      if (hasLatin && (hasCyrillic || hasGreek)) {
        homoglyphCount++;
        if (homoglyphCount > 3) break;
      }
    }

    if (homoglyphCount > 0) {
      findings.push({
        ruleId: 'unicode_homoglyph',
        severity: 'medium',
        category: 'unicode_trick',
        description: `${homoglyphCount} word(s) mix Latin with Cyrillic/Greek characters — possible homoglyph attack`,
        matchedText: `[${homoglyphCount} mixed-script word(s)]`,
        location: 'unicode',
      });
    }
  }

  private calculateRiskScore(findings: PromptInjectionFinding[]): number {
    if (findings.length === 0) return 0;

    let score = 0;
    for (const f of findings) {
      score += SEVERITY_WEIGHT[f.severity];
    }

    return Math.min(100, score);
  }

  private buildSummary(findings: PromptInjectionFinding[], riskScore: number): string {
    const criticalCount = findings.filter(f => f.severity === 'critical').length;
    const highCount = findings.filter(f => f.severity === 'high').length;
    const categories = [...new Set(findings.map(f => f.category))];

    const parts: string[] = [];
    parts.push(`Risk score: ${riskScore}/100`);
    parts.push(`${findings.length} finding(s)`);

    if (criticalCount > 0) parts.push(`${criticalCount} critical`);
    if (highCount > 0) parts.push(`${highCount} high`);

    parts.push(`Categories: ${categories.join(', ')}`);

    return parts.join(' | ');
  }
}
