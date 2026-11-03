// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: MIT-0

import { test } from 'node:test';
import * as assert from 'node:assert/strict';
import { AgentCoreHarnessRole } from '@aws/agentcore-cdk';
import * as cdk from 'aws-cdk-lib';
import { AspectPriority, Aspects } from 'aws-cdk-lib';
import { HIPAASecurityChecks } from 'cdk-nag';
import * as fs from 'fs';
import * as path from 'path';
import { InlinePolicyToManagedPolicy } from '../lib/aspects/inline-policy-to-managed-policy';

/** Read an installed package's version from node_modules (some packages don't export package.json). */
function packageVersion(name: string): string {
  // Tests run compiled from dist/test, so node_modules is two levels up.
  const manifest = path.join(__dirname, '..', '..', 'node_modules', ...name.split('/'), 'package.json');
  return JSON.parse(fs.readFileSync(manifest, 'utf8')).version as string;
}

/**
 * Regression test for the InlinePolicyToManagedPolicy aspect (HIPAA.Security-IAMNoInlinePolicy).
 *
 * AgentCoreHarnessRole is used because it is an L3-generated role that
 * synthesizes with no build toolchain and emits an inline policy. The role the
 * deployed runtime actually uses is scanned with the full AwsSolutions and
 * HIPAA packs in runtime-role.test.ts.
 */
test('aspect converts L3-generated inline policies to managed policies', () => {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'NagTestStack', {
    env: { account: '123456789012', region: 'us-east-1' },
  });

  // Mirrors src/agent/agentcore.json (project name + runtime name).
  new AgentCoreHarnessRole(stack, 'HarnessRole', {
    projectName: 'raihealthdiag',
    harness: {
      name: 'raihealthdiag',
      codeLocation: 'app/raihealthdiag/',
    },
  });

  // MUTATING priority: converts the L3-generated inline policies into customer
  // managed policies before the read-only rule packs inspect the tree. This is
  // the same aspect AgentCoreStack applies, so the scan reflects what deploys.
  Aspects.of(stack).add(new InlinePolicyToManagedPolicy(), { priority: AspectPriority.MUTATING });

  Aspects.of(app).add(new HIPAASecurityChecks({ verbose: true }), { priority: AspectPriority.READONLY });

  const assembly = app.synth();
  const artifact = assembly.getStackByName('NagTestStack');
  const messages = artifact.messages;

  const errors = messages.filter((m) => m.level === 'error');
  const warnings = messages.filter((m) => m.level === 'warning');
  const info = messages.filter((m) => m.level === 'info');

  const template = artifact.template as { Resources?: Record<string, { Type: string }> };
  const resources = Object.values(template.Resources ?? {});
  const inlinePolicies = resources.filter((r) => r.Type === 'AWS::IAM::Policy');
  const managedPolicies = resources.filter((r) => r.Type === 'AWS::IAM::ManagedPolicy');

  console.log('\n========================================');
  console.log('  cdk-nag Results (HIPAA.Security)');
  console.log('  Target: AgentCore harness execution role (raihealthdiag)');
  console.log('========================================');
  console.log('cdk-nag:           ' + packageVersion('cdk-nag'));
  console.log('aws-cdk-lib:       ' + packageVersion('aws-cdk-lib'));
  console.log('@aws/agentcore-cdk: ' + packageVersion('@aws/agentcore-cdk'));
  console.log('----------------------------------------');
  console.log('Errors:   ' + errors.length);
  console.log('Warnings: ' + warnings.length);
  console.log('Info:     ' + info.length);
  console.log('----------------------------------------');
  console.log('AWS::IAM::Policy (inline):   ' + inlinePolicies.length);
  console.log('AWS::IAM::ManagedPolicy:     ' + managedPolicies.length);

  if (errors.length > 0) {
    console.log('\n--- ERRORS ---');
    errors.forEach((e) => console.log('  [ERROR] ' + e.id + ': ' + e.entry.data));
  }
  if (warnings.length > 0) {
    console.log('\n--- WARNINGS ---');
    warnings.forEach((w) => console.log('  [WARN] ' + w.id + ': ' + w.entry.data));
  }
  if (info.length > 0) {
    console.log('\n--- INFO ---');
    info.forEach((i) => console.log('  [INFO] ' + i.id + ': ' + i.entry.data));
  }

  // No inline IAM policy may reach the deployed template.
  assert.equal(inlinePolicies.length, 0);
  assert.ok(managedPolicies.length > 0);

  // Fail if any unsuppressed finding remains.
  assert.equal(errors.length, 0);
});

/**
 * Control case. Documents the behaviour the aspect corrects: without it the L3
 * construct emits an inline AWS::IAM::Policy and cdk-nag raises
 * HIPAA.Security-IAMNoInlinePolicy. Guards against silently losing the fix.
 */
test('without the aspect, the L3 construct emits an inline policy and HIPAA flags it', () => {
  const app = new cdk.App();
  const stack = new cdk.Stack(app, 'ControlStack', {
    env: { account: '123456789012', region: 'us-east-1' },
  });

  new AgentCoreHarnessRole(stack, 'HarnessRole', {
    projectName: 'raihealthdiag',
    harness: { name: 'raihealthdiag', codeLocation: 'app/raihealthdiag/' },
  });

  Aspects.of(app).add(new HIPAASecurityChecks({ verbose: true }), { priority: AspectPriority.READONLY });

  const artifact = app.synth().getStackByName('ControlStack');
  const template = artifact.template as { Resources?: Record<string, { Type: string }> };
  const inlinePolicies = Object.values(template.Resources ?? {}).filter((r) => r.Type === 'AWS::IAM::Policy');

  const inlinePolicyFindings = artifact.messages.filter((m) =>
    String(m.entry.data).includes('HIPAA.Security-IAMNoInlinePolicy'),
  );

  console.log('\n--- CONTROL (aspect not applied) ---');
  console.log('AWS::IAM::Policy (inline):          ' + inlinePolicies.length);
  console.log('HIPAA.Security-IAMNoInlinePolicy:   ' + inlinePolicyFindings.length + ' finding(s)');
  inlinePolicyFindings.forEach((m) => console.log('  ' + m.entry.data));

  assert.ok(inlinePolicies.length > 0);
  assert.ok(inlinePolicyFindings.length > 0);
});
