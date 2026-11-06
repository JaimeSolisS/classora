import ts from 'typescript';

// #130 C3 (E4): hard-coded user-facing text in production source. Pure, on
// the TypeScript compiler API (no ESLint rule: a second no-restricted-syntax
// entry would replace the time guard's). Text counts as user-facing when it
// has two letters in a row (`\p{L}{2,}`), so punctuation (`·`, `—`, `→`),
// numbers and whitespace are fine.

export type FindingKind = 'jsx-text' | 'attribute' | 'call';

export interface Finding {
  file: string;
  kind: FindingKind;
  /** The attribute or the called function, for `attribute` and `call`. */
  name?: string;
  text: string;
  line: number;
}

const WORDS = /\p{L}{2,}/u;

/** JSX attributes whose value a person reads or hears. */
export const TEXT_ATTRIBUTES: ReadonlySet<string> = new Set([
  'aria-label',
  'aria-description',
  'aria-roledescription',
  'aria-valuetext',
  'title',
  'placeholder',
  'alt',
  'label',
]);

/** Functions whose first argument is shown or announced. */
const TEXT_CALLS: ReadonlySet<string> = new Set([
  'announce',
  'setAnnouncement',
  'useDocumentTitle',
]);

function isTextCall(name: string): boolean {
  return TEXT_CALLS.has(name) || /^toast/.test(name);
}

function calleeName(expression: ts.Expression): string | undefined {
  if (ts.isIdentifier(expression)) {
    return expression.text;
  }
  if (ts.isPropertyAccessExpression(expression)) {
    // `toast.error(...)`: the object names it.
    const object = expression.expression;
    if (ts.isIdentifier(object) && /^toast/.test(object.text)) {
      return object.text;
    }
    return expression.name.text;
  }
  return undefined;
}

/** A string or no-substitution template literal's text, else undefined. */
function literalText(node: ts.Node | undefined): string | undefined {
  if (!node) {
    return undefined;
  }
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
    return node.text;
  }
  if (ts.isJsxExpression(node)) {
    return literalText(node.expression);
  }
  if (ts.isParenthesizedExpression(node)) {
    return literalText(node.expression);
  }
  return undefined;
}

/** Every hard-coded user-facing text in `source` (a `.ts` or `.tsx` file). */
export function findHardcodedText(source: string, fileName: string): Finding[] {
  const kind = fileName.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(
    fileName,
    source,
    ts.ScriptTarget.Latest,
    true,
    kind,
  );
  const findings: Finding[] = [];
  const add = (node: ts.Node, found: Omit<Finding, 'file' | 'line'>) => {
    const { line } = file.getLineAndCharacterOfPosition(node.getStart(file));
    findings.push({ file: fileName, line: line + 1, ...found });
  };

  const visit = (node: ts.Node) => {
    if (ts.isJsxText(node)) {
      const text = node.text.trim();
      if (WORDS.test(text)) {
        add(node, { kind: 'jsx-text', text });
      }
    } else if (ts.isJsxAttribute(node)) {
      const name = node.name.getText(file);
      const text = literalText(node.initializer);
      if (TEXT_ATTRIBUTES.has(name) && text !== undefined && WORDS.test(text)) {
        add(node, { kind: 'attribute', name, text });
      }
    } else if (ts.isCallExpression(node)) {
      const name = calleeName(node.expression);
      const text = literalText(node.arguments[0]);
      if (
        name !== undefined &&
        isTextCall(name) &&
        text !== undefined &&
        WORDS.test(text)
      ) {
        add(node, { kind: 'call', name, text });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return findings;
}

/** Whether a source path is production code (not a test, story or kit). */
export function isProductionSource(path: string): boolean {
  return !(
    /\.(test|stories)\.[jt]sx?$/.test(path) ||
    /(^|\/)src\/test\//.test(path) ||
    /fixtures/i.test(path) ||
    /(^|\/)storyKit/.test(path) ||
    /(^|\/)storySession/.test(path) ||
    /\.d\.ts$/.test(path)
  );
}

export interface AllowedLiteral {
  file: string;
  text: string;
  reason: string;
}

/**
 * Hard-coded texts that are allowed, each with its reason. Starts empty
 * (the audit's baseline is 0).
 */
export const LITERAL_ALLOWLIST: readonly AllowedLiteral[] = [];
