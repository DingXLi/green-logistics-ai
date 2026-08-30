#!/usr/bin/env node
/**
 * Standalone URL state helper tests (iter #25)
 *
 * 验证 useUrlState hook 的纯函数逻辑 (makeParser / stringifyValue / readUrlState)
 * 不依赖 React 渲染, 不需要 jest/vitest
 *
 * Usage: node scripts/test_url_state.js
 */

'use strict'

// ============================================================
// Inline the helper functions from useUrlState.js (no React)
// ============================================================

function makeParser(type) {
  if (type === 'int') return (v) => {
    const n = parseInt(v, 10)
    return Number.isFinite(n) ? n : null
  }
  if (type === 'float') return (v) => {
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : null
  }
  if (type === 'bool') return (v) => {
    if (v === 'true' || v === '1') return true
    if (v === 'false' || v === '0') return false
    return null
  }
  return (v) => v
}

function stringifyValue(v) {
  if (v === null || v === undefined) return null
  return String(v)
}

// Mock URL state
class MockURL {
  constructor(search) {
    this.search = search || ''
    this.hash = ''
  }

  setSearch(search) {
    this.search = search
  }

  getParams() {
    return new URLSearchParams(this.search)
  }
}

// ============================================================
// Test framework (minimal assert + describe)
// ============================================================

let passed = 0
let failed = 0
let currentSuite = ''

function describe(name, fn) {
  currentSuite = name
  console.log(`\n${name}`)
  fn()
}

function test(name, fn) {
  try {
    fn()
    passed++
    console.log(`  ✓ ${name}`)
  } catch (e) {
    failed++
    console.log(`  ✗ ${name}`)
    console.log(`    ${e.message}`)
  }
}

function expect(actual) {
  return {
    toBe(expected) {
      if (actual !== expected) {
        throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
      }
    },
    toEqual(expected) {
      if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error(`expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`)
      }
    },
    toContain(substring) {
      if (!String(actual).includes(substring)) {
        throw new Error(`expected ${JSON.stringify(actual)} to contain ${JSON.stringify(substring)}`)
      }
    },
  }
}

// ============================================================
// Tests
// ============================================================

describe('makeParser - int', () => {
  test('parses valid int string', () => {
    expect(makeParser('int')('42')).toBe(42)
  })

  test('returns null for invalid int', () => {
    expect(makeParser('int')('abc')).toBe(null)
  })

  test('returns null for empty string', () => {
    // parseInt('') returns NaN, isFinite(NaN) is false, so returns null
    expect(makeParser('int')('')).toBe(null)
  })

  test('handles negative numbers', () => {
    expect(makeParser('int')('-7')).toBe(-7)
  })
})

describe('makeParser - float', () => {
  test('parses valid float string', () => {
    expect(makeParser('float')('3.14')).toBe(3.14)
  })

  test('parses scientific notation', () => {
    expect(makeParser('float')('1e3')).toBe(1000)
  })
})

describe('makeParser - bool', () => {
  test('"true" → true', () => {
    expect(makeParser('bool')('true')).toBe(true)
  })

  test('"1" → true', () => {
    expect(makeParser('bool')('1')).toBe(true)
  })

  test('"false" → false', () => {
    expect(makeParser('bool')('false')).toBe(false)
  })

  test('"0" → false', () => {
    expect(makeParser('bool')('0')).toBe(false)
  })

  test('other string → null', () => {
    expect(makeParser('bool')('maybe')).toBe(null)
  })
})

describe('makeParser - string (default)', () => {
  test('returns string as-is', () => {
    expect(makeParser('string')('hello')).toBe('hello')
    expect(makeParser('string')('hello world')).toBe('hello world')
  })
})

describe('stringifyValue', () => {
  test('string value', () => {
    expect(stringifyValue('overview')).toBe('overview')
  })

  test('integer value', () => {
    expect(stringifyValue(42)).toBe('42')
  })

  test('boolean true', () => {
    expect(stringifyValue(true)).toBe('true')
  })

  test('boolean false', () => {
    expect(stringifyValue(false)).toBe('false')
  })

  test('null → null', () => {
    expect(stringifyValue(null)).toBe(null)
  })

  test('undefined → null', () => {
    expect(stringifyValue(undefined)).toBe(null)
  })
})

describe('URL state behavior simulation', () => {
  test('read initial value from URL', () => {
    const url = new MockURL('?tab=network')
    const parser = makeParser('string')
    const raw = url.getParams().get('tab')
    const value = parser(raw)
    expect(value).toBe('network')
  })

  test('default value when param missing', () => {
    const url = new MockURL('')
    const raw = url.getParams().get('tab')
    expect(raw).toBe(null)
    const defaultValue = 'overview'
    const value = raw === null ? defaultValue : makeParser('string')(raw)
    expect(value).toBe('overview')
  })

  test('setting value to default removes param', () => {
    const params = new URLSearchParams('?tab=network')
    const newValue = 'overview'
    const defaultValue = 'overview'
    if (stringifyValue(newValue) === null || stringifyValue(newValue) === String(defaultValue)) {
      params.delete('tab')
    }
    expect(params.toString()).toBe('')
  })

  test('setting value to non-default adds param', () => {
    const params = new URLSearchParams('')
    const newValue = 'network'
    const defaultValue = 'overview'
    params.set('tab', stringifyValue(newValue))
    expect(params.toString()).toBe('tab=network')
  })

  test('multiple params coexist', () => {
    const params = new URLSearchParams('?foo=bar&tab=overview')
    params.set('tab', 'history')
    expect(params.get('foo')).toBe('bar')
    expect(params.get('tab')).toBe('history')
  })

  test('integer type preserves value', () => {
    const url = new MockURL('?n_periods=8')
    const raw = url.getParams().get('n_periods')
    const value = makeParser('int')(raw)
    expect(value).toBe(8)
    expect(typeof value).toBe('number')
  })
})

// ============================================================
// Summary
// ============================================================

console.log(`\n${'='.repeat(50)}`)
console.log(`Total: ${passed + failed} | ✓ Passed: ${passed} | ✗ Failed: ${failed}`)
console.log('='.repeat(50))

if (failed > 0) {
  process.exit(1)
}
