<?php
/**
 * Broker catalogue for the app's "Link account" picker.
 *
 * MetaApi connects to ANY MT4/MT5 broker, so this list is a convenience, not a
 * limit: the app always offers "Other broker" with a free-text server field.
 * It is sent to the app inside /v1/sync (and on /v1/brokers), so editing this
 * file updates every install on its next sync — no app release needed.
 *
 * Server names must match what the broker's own MT5 terminal shows under
 * Tools > Options > Server. Several brokers number their live servers
 * (Real2, MT5-4, Server3 …); the app lets the customer edit the name after
 * picking, so listing the common one is enough.
 */
declare(strict_types=1);

function gs_broker_catalog(): array
{
    $s = static fn(string $server, bool $demo) => ['server' => $server, 'demo' => $demo];
    return [
        ['name' => 'Exness', 'servers' => [
            $s('Exness-MT5Trial', true), $s('Exness-MT5Real', false),
        ], 'note' => 'Live accounts may be on a numbered server (Exness-MT5Real2, Real3 …). Copy the exact name from your terminal.'],

        ['name' => 'IC Markets', 'servers' => [
            $s('ICMarketsSC-Demo', true), $s('ICMarketsSC-MT5', false),
            $s('ICMarketsSC-MT5-2', false), $s('ICMarketsSC-MT5-4', false),
        ], 'note' => ''],

        ['name' => 'Pepperstone', 'servers' => [
            $s('Pepperstone-Demo', true), $s('Pepperstone-MT5-Live01', false),
        ], 'note' => 'Live server numbers vary by account; check the terminal.'],

        ['name' => 'XM', 'servers' => [
            $s('XMGlobal-MT5', false), $s('XMGlobal-MT5 2', false), $s('XMGlobal-MT5 3', false),
        ], 'note' => 'XM demo accounts use the same server family; pick the one your terminal shows.'],

        ['name' => 'FTMO (prop firm)', 'servers' => [
            $s('FTMO-Demo', true), $s('FTMO-Server', false),
            $s('FTMO-Server2', false), $s('FTMO-Server3', false),
        ], 'note' => 'Challenge and verification accounts are demo accounts on FTMO-Demo. Check FTMO\'s rules on third-party automation before linking.'],

        ['name' => 'RoboForex', 'servers' => [
            $s('RoboForex-Demo', true), $s('RoboForex-ECN', false),
            $s('RoboForex-Pro', false), $s('RoboForex-Prime', false),
        ], 'note' => ''],

        ['name' => 'Tickmill', 'servers' => [
            $s('Tickmill-Demo', true), $s('Tickmill-Live', false),
        ], 'note' => ''],

        ['name' => 'FBS', 'servers' => [
            $s('FBS-Demo', true), $s('FBS-Real', false),
        ], 'note' => ''],

        ['name' => 'Vantage', 'servers' => [
            $s('VantageInternational-Demo', true), $s('VantageInternational-Live', false),
        ], 'note' => ''],

        ['name' => 'Eightcap', 'servers' => [
            $s('Eightcap-Demo', true), $s('Eightcap-Real', false),
        ], 'note' => ''],

        ['name' => 'Fusion Markets', 'servers' => [
            $s('FusionMarkets-Demo', true), $s('FusionMarkets-Live', false),
        ], 'note' => ''],

        ['name' => 'HFM (HotForex)', 'servers' => [
            $s('HFMarketsGlobal-Demo', true), $s('HFMarketsGlobal-Live', false),
        ], 'note' => ''],

        ['name' => 'OctaFX', 'servers' => [
            $s('OctaFX-Demo', true), $s('OctaFX-Real', false),
        ], 'note' => ''],

        ['name' => 'FxPro', 'servers' => [
            $s('FxPro-MT5', false),
        ], 'note' => 'Demo accounts share the FxPro-MT5 family; check the terminal for the exact name.'],

        ['name' => 'BlackBull Markets', 'servers' => [
            $s('BlackBullMarkets-Demo', true), $s('BlackBullMarkets-Live', false),
        ], 'note' => ''],

        ['name' => 'Deriv', 'servers' => [
            $s('Deriv-Demo', true), $s('Deriv-Server', false),
        ], 'note' => ''],

        ['name' => 'MetaQuotes demo', 'servers' => [
            $s('MetaQuotes-Demo', true),
        ], 'note' => 'The free demo server built into every MT5 terminal. Good for a first test.'],
    ];
}
