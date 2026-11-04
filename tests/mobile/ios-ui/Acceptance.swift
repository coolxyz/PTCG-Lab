import XCTest
import Darwin

// Standalone UI runner for the owned simulator. No app database/IPC access,
// server tokens, external accounts, forced results, reset or uninstall.
final class Acceptance: XCTestCase {
    let app = XCUIApplication(bundleIdentifier: "org.ptcglab.mobile.preview")

    override func setUpWithError() throws {
        continueAfterFailure = false
        let url = Bundle(for: Acceptance.self).url(forResource: "PublicServer", withExtension: "json")!
        let config = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String:String]
        guard config["ownedSimulatorAcceptance"] == "true" else {
            throw XCTSkip("Explicit dedicated-simulator acceptance configuration required")
        }
        let origin = URLComponents(string: config["origin"] ?? "")
        XCTAssertEqual(origin?.scheme, "https")
        XCTAssertEqual(origin?.host, "127.0.0.1")
        XCTAssertTrue((20000...65535).contains(origin?.port ?? 0))
    }
    func capture(_ name: String) {
        let shot = XCTAttachment(screenshot: app.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }
    func reveal(_ element: XCUIElement) {
        for _ in 0..<8 {
            if element.exists && element.isHittable && element.frame.minY > 80 && element.frame.maxY < app.frame.maxY - 120 { return }
            let above = element.exists && element.frame.minY < 80
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: above ? 0.30 : 0.78))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: above ? 0.78 : 0.30)))
        }
    }
    func button(_ title: String) -> XCUIElement {
        app.buttons.matching(NSPredicate(format: "label == %@", title)).firstMatch
    }
    func tap(_ title: String) {
        let value = button(title)
        XCTAssertTrue(value.waitForExistence(timeout: 15), app.debugDescription)
        reveal(value)
        value.tap()
    }
    func openBattleMenu() {
        let menu = button("对局菜单")
        XCTAssertTrue(menu.waitForExistence(timeout: 15), app.debugDescription)
        menu.tap()
    }
    func tapBattleActions() {
        if button("收起操作").exists { return }
        let actions = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "操作")).firstMatch
        XCTAssertTrue(actions.waitForExistence(timeout: 15), app.debugDescription)
        actions.tap()
    }
    func saveThroughFiles() {
        let save = app.descendants(matching: .any).matching(
            NSPredicate(format: "label CONTAINS %@ OR label == %@", "存储到", "Save to Files")
        ).firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 20), app.debugDescription)
        save.tap()
        let commit = app.buttons.matching(NSPredicate(format: "label == %@ OR label == %@", "存储", "Save")).firstMatch
        XCTAssertTrue(commit.waitForExistence(timeout: 15), app.debugDescription)
        commit.tap()
    }
    func dismissKeyboard() {
        let done = app.buttons.matching(NSPredicate(format: "label == %@ OR label == %@", "Done", "完成")).firstMatch
        if done.exists && done.isHittable { done.tap() }
    }
    func selectHTML(_ label: String, _ value: String) {
        let select = app.otherElements[label]
        reveal(select)
        XCTAssertTrue(select.exists, app.debugDescription)
        select.tap()
        let wheel = app.pickerWheels.firstMatch
        if wheel.waitForExistence(timeout: 2) {
            wheel.adjust(toPickerWheelValue: value)
        } else {
            let option = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", value)).firstMatch
            XCTAssertTrue(option.waitForExistence(timeout: 5), app.debugDescription)
            option.tap()
        }
        dismissKeyboard()
    }
    func test04ConfigureNativeTLSGuestAndSharedDeck() throws {
        let url = Bundle(for: Acceptance.self).url(forResource: "PublicServer", withExtension: "json")!
        let config = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String:String]
        app.launch()
        XCTAssertTrue(button("对战").waitForExistence(timeout: 45))
        button("对战").tap()
        let origin = app.textFields["服务器地址"]
        if !origin.exists {
            let settings = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "对战服务器")).firstMatch
            settings.tap()
        }
        XCTAssertTrue(origin.waitForExistence(timeout: 10), app.debugDescription)
        let current = origin.value as? String ?? ""
        XCTAssertTrue(current.isEmpty || current == "https://battle.example.com" || current == config["origin"])
        if current != config["origin"] {
            origin.tap()
            origin.typeText(config["origin"]!)
            dismissKeyboard()
            tap("局域网服务器的 CA 证书")
            let certificate = app.textViews["服务器公开 CA 证书"]
            reveal(certificate)
            certificate.tap()
            certificate.typeText(config["caPem"]!)
            XCTAssertEqual(certificate.value as? String, config["caPem"]!)
            dismissKeyboard()
            tap("保存服务器")
        }
        let guest = button("创建游客身份")
        if guest.waitForExistence(timeout: 20) {
            XCTAssertTrue(guest.isEnabled, app.debugDescription)
            reveal(guest)
            guest.tap()
        }
        let deck = app.otherElements["平台已有卡组"]
        XCTAssertTrue(deck.waitForExistence(timeout: 20), app.debugDescription)
        selectHTML("平台已有卡组", "导入的卡组")
        let admit = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "校验并使用此版本")).firstMatch
        reveal(admit)
        XCTAssertTrue(admit.isEnabled, app.debugDescription)
        admit.tap()
        let ready = app.staticTexts["卡组版本已就绪"]
        XCTAssertTrue(ready.waitForExistence(timeout: 30), app.debugDescription)
        capture("final-06-native-guest-deck-admitted")
    }
    func test05JoinRoomAndInspectInitialDecision() throws {
        let url = Bundle(for: Acceptance.self).url(forResource: "PublicServer", withExtension: "json")!
        let config = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as! [String:String]
        app.launch()
        XCTAssertTrue(button("对战").waitForExistence(timeout: 45))
        button("对战").tap()
        let code = app.textFields["房间码"]
        XCTAssertTrue(code.waitForExistence(timeout: 20), app.debugDescription)
        reveal(code)
        code.tap()
        code.typeText(config["inviteCode"]!)
        dismissKeyboard()
        tap("加入房间")
        XCTAssertTrue(app.staticTexts["已准备"].waitForExistence(timeout: 15), app.debugDescription)
        tap("准备对战")
        let control = button("取得操作权")
        XCTAssertTrue(control.waitForExistence(timeout: 30), app.debugDescription)
        reveal(control)
        control.tap()
        capture("native-ios-initial-match")
        print("INITIAL_ACTION_LABELS", app.buttons.allElementsBoundByIndex.map{$0.label})
        print("INITIAL_SWITCH_LABELS", app.switches.allElementsBoundByIndex.map{$0.label})
    }
    func test06NaturalMatchAndOwnReplay() throws {
        app.activate()
        let control = button("取得操作权")
        XCTAssertTrue(control.waitForExistence(timeout: 20))
        reveal(control)
        control.tap()
        tapBattleActions()
        let end = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@ OR label CONTAINS %@", "你赢了！", "对手 获胜")).firstMatch
        let deadline = Date().addingTimeInterval(1200)
        var actions = 0
        var lastSubmittedSequence = -1
        while !end.exists && Date() < deadline {
            let seq=pairedSequence()
            if seq<0 || seq==lastSubmittedSequence {pairedWait(0.5);continue}
            if app.staticTexts["轮到你操作"].exists { tapBattleActions() }
            let pass=app.buttons.matching(NSPredicate(format:"label == %@ AND enabled == true","结束回合")).firstMatch
            let first = button("选择先攻")
            let confirm = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "确认选择")).firstMatch
            if pass.exists {
                reveal(pass);lastSubmittedSequence=seq;pass.tap();actions += 1
                let advanced=XCTNSPredicateExpectation(predicate:NSPredicate{_,_ in self.pairedSequence()>seq},object:nil)
                XCTAssertEqual(XCTWaiter.wait(for:[advanced],timeout:25),.completed)
                pairedWait(0.6)
            } else if first.exists && first.isEnabled {
                reveal(first); first.tap(); actions += 1
            } else if button("已查看重抽手牌，继续").exists && button("已查看重抽手牌，继续").isEnabled {
                tap("已查看重抽手牌，继续")
            } else if button("补偿抽 0 张").exists && button("补偿抽 0 张").isEnabled {
                tap("补偿抽 0 张")
            } else if button("已查看，继续结算").exists && button("已查看，继续结算").isEnabled {
                tap("已查看，继续结算")
            } else if confirm.exists {
                if confirm.isEnabled {
                    reveal(confirm); confirm.tap(); actions += 1
                } else {
                    let candidates = app.switches.matching(NSPredicate(format: "label CONTAINS %@ OR label CONTAINS %@", "梦幻", "Mew")).allElementsBoundByIndex
                    if let card = candidates.last(where:{$0.isEnabled && ($0.value as? String) != "1"}) {
                        reveal(card); card.tap()
                    }
                }
            } else {
                RunLoop.current.run(until: Date().addingTimeInterval(0.5))
            }
            if actions > 0 && actions % 10 == 0 { print("IOS_GUI_INTERACTIONS", actions) }
        }
        XCTAssertTrue(end.exists, app.buttons.allElementsBoundByIndex.map{$0.label}.joined(separator:" | "))
        capture("ios-natural-match-finished")
        openBattleMenu(); tap("逐步回放")
        let complete = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "回放已完整加载")).firstMatch
        XCTAssertTrue(complete.waitForExistence(timeout: 120), app.debugDescription)
        print("IOS_REPLAY_STATUS",complete.label)
        capture("ios-own-replay-loaded")
    }
    func test07FinalCandidateIdentityHistoryReplayAndNotesReopen() throws {
        app.launch()
        XCTAssertTrue(button("对战").waitForExistence(timeout: 45))
        button("对战").tap()
        let watch = button("观看回放")
        XCTAssertTrue(watch.waitForExistence(timeout: 25), app.debugDescription)
        XCTAssertFalse(button("创建游客身份").exists)
        capture("final-07-history-after-update")
        reveal(watch); watch.tap()
        let complete = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "回放已完整加载")).firstMatch
        XCTAssertTrue(complete.waitForExistence(timeout: 120), app.debugDescription)
        reveal(complete)
        capture("final-07-own-replay-reopened")
        app.terminate()
        app.launch()
        XCTAssertTrue(button("卡库").waitForExistence(timeout: 45))
        let card = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "查看梦幻ex ")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 15))
        card.tap()
        selectHTML("收藏品相", "良好")
        let notes = app.textViews["收藏备注"]
        reveal(notes)
        XCTAssertEqual(notes.value as? String, "P8-F edited")
        XCTAssertEqual(app.textFields["持有数量"].value as? String, "0")
        capture("final-07-notes-after-relaunch")
    }
    func test02CollectionCSVImportDuplicateAndExport() throws {
        app.launch()
        XCTAssertTrue(button("收藏").waitForExistence(timeout: 45), app.debugDescription)
        button("收藏").tap()
        tap("选择收藏 CSV")
        let csv = app.descendants(matching: .any).matching(
            NSPredicate(format: "label CONTAINS %@", "02-collection")
        ).firstMatch
        XCTAssertTrue(csv.waitForExistence(timeout: 12), app.debugDescription)
        csv.tap()
        tap("预览收藏导入")
        tap("确认增加到收藏")
        let notice = app.staticTexts.matching(NSPredicate(format:
            "label CONTAINS %@ OR label CONTAINS %@", "收藏已导入", "这批资料已导入")).firstMatch
        XCTAssertTrue(notice.waitForExistence(timeout: 15), app.debugDescription)
        capture("final-06-csv-imported")
        tap("预览收藏导入")
        tap("确认增加到收藏")
        XCTAssertTrue(app.staticTexts["这批资料已导入，未重复增加数量"].waitForExistence(timeout: 15), app.debugDescription)
        capture("final-06-csv-duplicate")
        tap("分享收藏 CSV")
        saveThroughFiles()
        XCTAssertTrue(button("选择收藏 CSV").waitForExistence(timeout: 15), app.debugDescription)
        capture("final-06-csv-share-returned")
    }
    func test03EditedNotesSurviveBatchUndo() throws {
        app.launch()
        XCTAssertTrue(button("收藏").waitForExistence(timeout: 45), app.debugDescription)
        button("收藏").tap()
        let card = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "查看梦幻ex ")).firstMatch
        reveal(card)
        XCTAssertTrue(card.exists, app.debugDescription)
        card.tap()
        let condition = app.otherElements["收藏品相"]
        reveal(condition)
        XCTAssertTrue(condition.exists, app.debugDescription)
        condition.tap()
        let wheel = app.pickerWheels.firstMatch
        if wheel.waitForExistence(timeout: 2) {
            wheel.adjust(toPickerWheelValue: "良好")
        } else {
            let good = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", "良好")).firstMatch
            XCTAssertTrue(good.waitForExistence(timeout: 5), app.debugDescription)
            good.tap()
        }
        let done = app.buttons.matching(NSPredicate(format: "label == %@ OR label == %@", "Done", "完成")).firstMatch
        if done.exists { done.tap() }
        let notes = app.textViews["收藏备注"]
        reveal(notes)
        XCTAssertTrue(notes.exists, app.debugDescription)
        notes.tap()
        notes.press(forDuration: 1.1)
        let all = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@ OR label == %@", "全选", "Select All")).firstMatch
        XCTAssertTrue(all.waitForExistence(timeout: 5), app.debugDescription)
        all.tap()
        notes.typeText("P8-F edited")
        XCTAssertEqual(notes.value as? String, "P8-F edited")
        if done.exists { done.tap() }
        tap("保存此品相")
        XCTAssertTrue(app.staticTexts["此品相已保存"].waitForExistence(timeout: 10), app.debugDescription)
        capture("final-06-notes-edited")
        let close = button("关闭卡牌详情")
        for _ in 0..<8 {
            if close.isHittable && close.frame.minY > 80 { break }
            app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.28))
                .press(forDuration: 0.05, thenDragTo: app.coordinate(withNormalizedOffset: CGVector(dx: 0.96, dy: 0.80)))
        }
        close.tap()
        let batches = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "导入批次")).firstMatch
        reveal(batches)
        XCTAssertTrue(batches.exists, app.debugDescription)
        batches.tap()
        tap("撤销此批次")
        XCTAssertTrue(app.staticTexts["已撤销导入数量；之后修改的备注和愿望单会保留"].waitForExistence(timeout: 15), app.debugDescription)
        capture("final-06-batch-undone")
    }
    func test01ReopenDeckAndPresentSystemShare() throws {
        app.launch()
        XCTAssertTrue(button("卡组").waitForExistence(timeout: 45), app.debugDescription)
        capture("final-06-launch")
        button("卡组").tap()
        let deck = app.switches.matching(NSPredicate(format: "label CONTAINS %@", "导入的卡组")).firstMatch
        XCTAssertTrue(deck.waitForExistence(timeout: 15), app.debugDescription)
        deck.tap()
        let share = button("分享卡组文件")
        reveal(share)
        XCTAssertTrue(share.exists && share.isHittable, app.debugDescription)
        capture("final-06-deck-reopened")
        share.tap()
        let save = app.descendants(matching: .any).matching(
            NSPredicate(format: "label CONTAINS %@ OR label == %@", "存储到", "Save to Files")
        ).firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 20), app.debugDescription)
        capture("final-06-system-share")
        save.tap()
        capture("final-06-save-provider")
        let saveFile = app.buttons.matching(NSPredicate(format: "label == %@ OR label == %@", "存储", "Save")).firstMatch
        XCTAssertTrue(saveFile.waitForExistence(timeout: 15), app.debugDescription)
        saveFile.tap()
        XCTAssertTrue(share.waitForExistence(timeout: 15), app.debugDescription)
        capture("final-06-share-returned")
    }
    func test08FilePickerCancelAndInvalidDeckLeaveSavedDeckIntact() throws {
        app.launch()
        XCTAssertTrue(button("卡组").waitForExistence(timeout: 45))
        button("卡组").tap()
        let saved = app.switches.matching(NSPredicate(format: "label CONTAINS %@", "导入的卡组 60 张")).firstMatch
        XCTAssertTrue(saved.waitForExistence(timeout: 15), app.debugDescription)
        tap("导入卡组文本")
        tap("选择卡组文件")
        let cancel = app.buttons.matching(NSPredicate(format: "label == %@ OR label == %@", "取消", "Cancel")).firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 12), app.debugDescription)
        cancel.tap()
        XCTAssertTrue(button("选择卡组文件").waitForExistence(timeout: 12))
        XCTAssertTrue(button("选择卡组文件").isEnabled)
        XCTAssertFalse(button("检查并新建卡组").isEnabled)
        capture("final-07-file-cancelled")
        tap("选择卡组文件")
        let invalid = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "03-rejected-deck")).firstMatch
        XCTAssertTrue(invalid.waitForExistence(timeout: 12), app.debugDescription)
        invalid.tap()
        tap("检查并新建卡组")
        let error = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "本机目录没有收录的印刷版本")).firstMatch
        XCTAssertTrue(error.waitForExistence(timeout: 15), app.debugDescription)
        XCTAssertTrue(saved.exists)
        XCTAssertEqual(app.switches.matching(NSPredicate(format: "label CONTAINS %@", "导入的卡组")).count, 1)
        capture("final-07-invalid-deck-rejected")
    }
    // Requires only the owned simulator and 04-deck-exchange.csv in its Documents.
    // The imported draft is test-only; no existing deck is selected for deletion.
    func test09DesktopCSVFileImportShareAndDeleteDraft() throws {
        app.launch()
        XCTAssertTrue(button("卡组").waitForExistence(timeout: 45), app.debugDescription)
        button("卡组").tap()
        tap("选择卡组文件")
        let csv = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "04-deck-exchange")).firstMatch
        if !csv.waitForExistence(timeout: 2) {
            let browse = app.buttons["浏览"]
            XCTAssertTrue(browse.waitForExistence(timeout: 5), app.debugDescription)
            browse.tap()
        }
        XCTAssertTrue(csv.waitForExistence(timeout: 15), app.debugDescription)
        capture("deck-csv-system-picker")
        csv.tap()
        tap("检查并新建卡组")
        let energy = app.textFields["CN:CSM2.1C:044数量"]
        XCTAssertTrue(energy.waitForExistence(timeout: 15), app.debugDescription)
        XCTAssertEqual(energy.value as? String, "56")
        XCTAssertEqual(app.textFields["CN:CSVM2cC:003数量"].value as? String, "4")
        capture("deck-csv-imported")
        tap("分享卡组文件")
        capture("deck-csv-system-share")
        saveThroughFiles()
        XCTAssertTrue(app.staticTexts["卡组已通过系统分享"].waitForExistence(timeout: 15), app.debugDescription)
        tap("删除卡组")
        capture("deck-delete-confirmation")
        tap("取消删除")
        XCTAssertTrue(energy.exists)
        tap("删除卡组")
        tap("确认删除卡组")
        XCTAssertTrue(app.staticTexts["卡组已删除，冻结版本和历史对局已保留"].waitForExistence(timeout: 15), app.debugDescription)
        capture("deck-test-draft-deleted")
    }
    func pairedMark(_ value: String) { print(value); fflush(nil) }
    func pairedWait(_ seconds: TimeInterval) { RunLoop.current.run(until: Date().addingTimeInterval(seconds)) }
    func pairedSequence() -> Int {
        let header=app.staticTexts.matching(NSPredicate(format:"label MATCHES %@",".* · 回合 [0-9]+ · 第 [0-9]+ 步")).firstMatch
        guard header.waitForExistence(timeout:5) else {return -1}
        let label=header.label
        let source=label as NSString
        let pattern=try! NSRegularExpression(pattern:"第 ([0-9]+) 步")
        guard let match=pattern.firstMatch(in:label,range:NSRange(location:0,length:source.length)) else {return -1}
        return Int(source.substring(with:match.range(at:1)))!
    }
    func pairedPass() {
        tap("结束回合")
    }
    func test23ActualBackgroundAndMissedUpdateRecovery() throws {
        app.activate();tap("取得操作权")
        tapBattleActions()
        let pass=button("结束回合")
        XCTAssertTrue(pass.waitForExistence(timeout:20));XCTAssertTrue(pass.isEnabled)
        let seq=pairedSequence();pairedPass();pairedWait(2)
        let before=pairedSequence();XCTAssertGreaterThan(before,seq)
        capture("atomic-ios-before-background")
        XCUIDevice.shared.press(.home);pairedMark("P8PAIR_ATOMIC_BACKGROUND_BEGIN \(before)")
        pairedWait(45)
        app.activate()
        let readOnly=app.staticTexts.matching(NSPredicate(format:"label CONTAINS %@","只读观察")).firstMatch
        XCTAssertTrue(readOnly.waitForExistence(timeout:15),app.debugDescription)
        let after=pairedSequence();XCTAssertGreaterThan(after,before)
        capture("atomic-ios-foreground-missed-update-restored")
        tap("取得操作权")
        pairedMark("P8PAIR_ATOMIC_BACKGROUND_PASS \(before) \(after)")
    }
    func test24AcceptedUnknownCommandSurvivesProcessTermination() throws {
        app.activate();tap("取得操作权")
        tapBattleActions()
        let ready=button("结束回合")
        let enabled=XCTNSPredicateExpectation(predicate:NSPredicate{_,_ in ready.exists && ready.isEnabled},object:nil)
        XCTAssertEqual(XCTWaiter.wait(for:[enabled],timeout:20),.completed,app.debugDescription)
        let seq=pairedSequence();pairedMark("P8PAIR_ARM_UNKNOWN \(seq)");pairedWait(3)
        pairedPass();pairedWait(2);capture("atomic-ios-unknown-before-termination")
        app.terminate();pairedMark("P8PAIR_TERMINATED_UNKNOWN");pairedWait(3)
        app.launch();XCTAssertTrue(button("对战").waitForExistence(timeout:45));button("对战").tap()
        let recover=button("恢复待确认操作")
        if !button("对局菜单").waitForExistence(timeout:8) {
            let pending=app.buttons.matching(NSPredicate(format:"label BEGINSWITH %@","恢复待确认对局")).firstMatch
            if pending.exists {reveal(pending);pending.tap()} else {tap("继续对局")}
        }
        openBattleMenu()
        XCTAssertTrue(recover.waitForExistence(timeout:20),app.debugDescription)
        XCTAssertFalse(button("创建游客身份").exists)
        capture("atomic-ios-original-pending-survived-process-exit")
        tap("恢复待确认操作");pairedWait(3)
        XCTAssertFalse(recover.exists,app.debugDescription)
        button("关闭菜单").tap()
        XCTAssertGreaterThan(pairedSequence(),seq)
        tap("取得操作权");capture("atomic-ios-original-receipt-recovered")
        pairedMark("P8PAIR_ATOMIC_UNKNOWN_PASS \(seq) \(pairedSequence())")
    }
    func test25NativeNetworkOutageAndCursorRecovery() throws {
        app.activate();openBattleMenu();tap("刷新局面");pairedWait(2);tap("取得操作权")
        let before=pairedSequence();XCTAssertGreaterThanOrEqual(before,0)
        pairedMark("P8PAIR_NETWORK_OUTAGE \(before)");pairedWait(2)
        openBattleMenu();tap("刷新局面");pairedWait(9);openBattleMenu();tap("刷新局面");pairedWait(3)
        XCTAssertGreaterThanOrEqual(pairedSequence(),before)
        XCTAssertFalse(button("创建游客身份").exists)
        tap("取得操作权");capture("atomic-ios-network-and-cursor-recovered")
        pairedMark("P8PAIR_ATOMIC_NETWORK_PASS \(before) \(pairedSequence())")
    }
    func test26OwnReplaySeekBackAndPlaybackReadOnly() throws {
        app.activate()
        let complete=app.staticTexts.matching(NSPredicate(format:"label CONTAINS %@","回放已完整加载")).firstMatch
        XCTAssertTrue(complete.waitForExistence(timeout:120),app.debugDescription)
        XCTAssertFalse(button("认输").exists);XCTAssertFalse(button("取得操作权").exists)
        let configURL=Bundle(for:Acceptance.self).url(forResource:"PublicServer",withExtension:"json")!
        let config=try JSONSerialization.jsonObject(with:Data(contentsOf:configURL)) as! [String:String]
        let final=try XCTUnwrap(Int(config["expectedFinalSequence"] ?? ""))
        XCTAssertTrue((1...5000).contains(final))
        let slider=app.sliders["回放步数"];reveal(slider)
        slider.coordinate(withNormalizedOffset:CGVector(dx:0.01,dy:0.5)).tap()
        XCTAssertTrue(app.staticTexts["回放 · 第 0 步"].waitForExistence(timeout:10))
        tap("回放下一步")
        XCTAssertTrue(app.staticTexts["回放 · 第 1 步"].waitForExistence(timeout:10))
        reveal(slider);XCTAssertTrue(slider.exists)
        slider.coordinate(withNormalizedOffset:CGVector(dx:0.99,dy:0.5)).tap()
        XCTAssertTrue(app.staticTexts["回放 · 第 \(final) 步"].waitForExistence(timeout:10),app.debugDescription)
        capture("paired-ios-replay-final-frame")
        tap("回放上一步")
        XCTAssertTrue(app.staticTexts["回放 · 第 \(final-1) 步"].waitForExistence(timeout:10))
        tap("播放回放")
        XCTAssertTrue(app.staticTexts["回放 · 第 \(final) 步"].waitForExistence(timeout:15),app.debugDescription)
        pairedWait(1)
        XCTAssertFalse(button("播放回放").isEnabled)
        XCTAssertFalse(button("认输").exists)
        capture("paired-ios-replay-playback-finished-readonly")
        pairedMark("P8PAIR_REPLAY_CONTROLS_PASS 0 1 \(final) \(final-1) \(final)")
    }
}
