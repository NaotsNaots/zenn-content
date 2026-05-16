---
title: "nRF52840 + BLE + SGP40 + 電子ペーパーで室内空気質モニターを自作した【VOC／eCO₂をArduinoで取得】"
emoji: "🌿"
type: "tech"
topics: ["BLE", "nRF52840", "Arduino", "組み込み", "IoT"]
published: true
---

## はじめに

室内の空気が悪くなると、集中力が落ちたり眠くなったりします。
「換気のタイミングを数値で見たい」——そんな動機で、**BLE通信＋電子ペーパー表示の小型空気質モニター**を個人で自作しました。

本業では10年間、大手車載メーカーで機構設計・組み込みソフト（Bluetooth / AOSP）を担当しています。
その経験を活かして、設計〜ファームウェア〜ドキュメントまで一気通貫で仕上げたのがこのプロジェクトです。

**構成のポイント**

- MCU: Nordic **nRF52840** 開発ボード
- センサー: Sensirion **SGP40**（VOC raw値 → VOCインデックス＋eCO₂推定値）
- 表示: **電子ペーパー**（e-Paper）
- 電源: 単三電池 1本 → 3.3V 昇圧（常時給電不要）
- 通信: **BLE Peripheral**（スマホの nRF Connect で確認）
- 実装: **Arduino**

GitHubにソースコード・設計ドキュメントを公開しています。

---

## SGP40 でVOCとeCO₂を取得する仕組み

### SGP40とは

Sensirion製のMOX（金属酸化物）型センサーです。I²C接続で**VOCのraw値**を出力します。
ポイントは「センサーがそのままCO₂を測定するわけではない」という点です。

### Sensirion VOC Gas Index Algorithm

SGP40のraw値をそのまま使っても意味のある数値にはなりません。
Sensirionが提供する**VOC Gas Index Algorithm**ライブラリに通すことで、以下の2つの値を算出します。

| 出力値 | 説明 |
|--------|------|
| **VOCインデックス（1〜500）** | 空気中のVOC濃度を正規化した指標。1が最良、500が最悪 |
| **eCO₂（推定値、ppm）** | VOCデータからアルゴリズムで推定したCO₂相当値。NDIRセンサーの直接計測ではなく推定値 |

:::message
**eCO₂は推定値です**
SCD41のようなNDIRセンサーによる直接計測とは異なり、SGP40 + アルゴリズムによる推定値です。傾向の把握・換気タイミングの目安として活用できます。
:::

この仕組みにより、**1つのセンサーでVOCと疑似CO₂の両方をユーザーに提供**できるのがこの構成の肝です。

### 表示する値（ユーザー向け）

| 表示値 | 単位 | 説明 |
|--------|------|------|
| VOCインデックス | 1〜500 | 空気の質の総合指標 |
| eCO₂ | ppm | アルゴリズム推定のCO₂相当値 |

---

## システム構成

### ブロック図

```
[SGP40（I²C）] ─────┐
                      ├──→ [nRF52840] ──BLE──→ [スマートフォン（nRF Connect）]
[電子ペーパー（SPI）]─┘        │
                               └──→ 昇圧回路（単三×1本 → 3.3V）
```

### ハードウェア一覧（BOM）

| 部品 | 仕様 | 役割 |
|------|------|------|
| MCU | Nordic nRF52840 開発ボード | メイン処理・BLE |
| VOCセンサー | Sensirion SGP40 | raw値 → VOC / eCO₂算出 |
| 電子ペーパー | Waveshare 2.9inch e-Paper V4 (296×128px, BWR 3色) | ローカル表示 |
| 昇圧DC-DC | TOREX XCL102（インダクター内蔵・3.3V出力） | 単三電池 → 3.3V |
| 単三電池 | ×1本 | 電源 |
| ブレッドボード | 標準 | プロトタイプ |

---

## ソフトウェア設計

### 開発環境

- **IDE**: Arduino IDE（nRF52840 Board Package を追加）
- **BLEライブラリ**: ArduinoBLE
- **センサーライブラリ**: Sensirion Arduino Core（`SensirionI2CSgp40`）
- **アルゴリズムライブラリ**: Sensirion Gas Index Algorithm（`VOCGasIndexAlgorithm`）

### BLEの役割分担

| 役割 | デバイス |
|------|----------|
| **Peripheral（自作デバイス）** | nRF52840ボード |
| **Central（確認用）** | スマートフォン（nRF Connect） |

BLE Characteristicを2つ用意し、VOCインデックスとeCO₂をそれぞれ別で通知します。

### 実装のポイント

```cpp
#include <ArduinoBLE.h>
#include <SensirionI2CSgp40.h>
#include <VOCGasIndexAlgorithm.h>

SensirionI2CSgp40 sgp40;
VOCGasIndexAlgorithm vocAlgorithm;

BLEService airService("181A");  // Environmental Sensing Service
BLEShortCharacteristic vocCharacteristic("2BD3", BLERead | BLENotify);  // VOCインデックス
BLEShortCharacteristic eco2Characteristic("2B8C", BLERead | BLENotify); // eCO₂推定値

void setup() {
  Wire.begin();
  sgp40.begin(Wire);

  BLE.begin();
  BLE.setLocalName("AirMonitor");
  airService.addCharacteristic(vocCharacteristic);
  airService.addCharacteristic(eco2Characteristic);
  BLE.addService(airService);
  BLE.advertise();

  // 電子ペーパー初期化
  // epd.init();
}

void loop() {
  uint16_t rawValue;

  // SGP40からraw値を取得（温湿度補正なし・固定値で計算）
  sgp40.measureRawSignal(0x8000, 0x6666, rawValue);

  // VOC Gas Index Algorithmでインデックスと推定eCO₂を算出
  int32_t vocIndex = vocAlgorithm.process(rawValue);
  int32_t eco2Estimated = vocAlgorithm.getEco2(); // eCO₂推定値（ppm）

  // BLE Characteristicに書き込み
  vocCharacteristic.writeValue((int16_t)vocIndex);
  eco2Characteristic.writeValue((int16_t)eco2Estimated);

  // 電子ペーパー更新（値が変化したときのみ）
  // updateDisplay(vocIndex, eco2Estimated);

  delay(1000);
}
```

> 実際の完全なコードは GitHub を参照してください。

---

## 省電力の工夫

電子ペーパーは**表示を保持する電力がゼロ**なため、バッテリー駆動デバイスと相性抜群です。

- **電子ペーパー（Waveshare 2.9" BWR）**: 値が変化したときのみ更新。BWR（黒・白・赤）3色を活かし、VOCインデックスが高い（空気が悪い）ときは**赤色で警告表示**する設計にしている。電子ペーパーは表示を保持しても電力ゼロのため、更新頻度を下げるほど省電力になる
- **BLE**: Advertise間隔を長めに設定してアイドル電流を削減
- **昇圧DC-DC（TOREX XCL102）**: インダクターをワンパッケージに内蔵した超小型DC-DCコンバーター。単三電池1本（約0.9〜1.5V）から3.3Vへ昇圧。チップ自体が極めてコンパクトで、将来的な基板小型化にも貢献する部品選定

---

## 動作確認

### nRF Connectでの確認手順

1. スマートフォンに **nRF Connect**（Nordic公式アプリ）をインストール
2. **SCAN** → 「AirMonitor」を検出 → **CONNECT**
3. Environmental Sensing Service → Characteristicが2つ確認できる
4. それぞれタップ → **VOCインデックス**と**eCO₂推定値**をリアルタイムで読める

（実機の写真・nRF Connect画面のスクリーンショットをここに挿入）

---

## ハマったところ

### SGP40のウォームアップが必要

SGP40は起動直後は正確な値が出ません。Sensirionの**VOC Gas Index Algorithmは約60秒のウォームアップ**が必要で、この間はインデックスが安定しません。起動後すぐは電子ペーパーに「warming up...」と表示させることで解決しました。

### BLE Characteristicの型ミス

最初`BLEIntCharacteristic`（32bit）を使っていたため、nRF Connectで読み取った値が想定外の数値になっていました。VOCインデックス（1〜500）とeCO₂（400〜5000程度）はどちらもint16で収まるため、`BLEShortCharacteristic`に変更して正常に読めるようになりました。

---

## 今後の予定

- [ ] SHT40と組み合わせて**温湿度を実測値で補正**（より正確なVOC/eCO₂算出）
- [ ] ブレッドボード → **自前PCB**（KiCad）
- [ ] **LiPo + 充電回路**に変更してさらに小型化
- [ ] 振動フィードバック（空気質が悪化したとき振動で通知）

---

## GitHub

ソースコード・設計ドキュメント（要件定義〜詳細設計）はこちら：

🔗 [NaotsNaots / BLE-ePaper-Co2-monitor](https://github.com/NaotsNaots/BLE-ePaper-Co2-monitor)

---

## おわりに

「CO₂が高いと眠くなる」という体感から始まったプロジェクトですが、SGP40 + Sensirionのアルゴリズムでこれだけの情報量を1センサーから取得できるのは発見でした。

nRF52840 + ArduinoBLEの組み合わせはBLE Peripheralの実装がスムーズで、電子ペーパーの省電力特性もバッテリー駆動との相性が抜群です。

本業の組み込み経験（BLEスタック・設計ドキュメント）がそのまま活きた開発でした。

---

## お仕事のご依頼

BLE / 組み込み開発・設計レビューのご相談はお気軽にどうぞ。

| プラットフォーム | リンク |
|---|---|
| 🐥 Fiverr（英語・海外） | [swallow_eng](https://www.fiverr.com/swallow_eng) |
| 🥥 ランサーズ（日本語） | [swallow_pg386](https://www.lancers.jp/profile/swallow_pg386) |
