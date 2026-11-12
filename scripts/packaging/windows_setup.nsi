Unicode true
RequestExecutionLevel user
CRCCheck force
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "x64.nsh"
!include "FileFunc.nsh"

!define PRODUCT "PTCG Lab Preview"
!define VERSION "${SETUP_VERSION}"
!define INSTALLROOT "$LOCALAPPDATA\Programs\${PRODUCT}"
!define PROFILE "$LOCALAPPDATA\org.ptcglab.desktop.preview"
!define APP "PTCG Lab Preview.exe"
!define UNINSTALLKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\org.ptcglab.desktop.preview"

Name "${PRODUCT} ${VERSION}"
InstallDir "${INSTALLROOT}"
OutFile "${SETUP_OUTPUT}"
Icon "${SETUP_ICON}"
UninstallIcon "${SETUP_ICON}"
VIProductVersion "1.1.0.0"
VIAddVersionKey "ProductName" "${PRODUCT}"
VIAddVersionKey "ProductVersion" "${VERSION}"
VIAddVersionKey "FileVersion" "${VERSION}"
VIAddVersionKey "FileDescription" "PTCG Lab Windows x64 Setup"
VIAddVersionKey "LegalCopyright" "PTCG Lab contributors"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_INSTFILES
!define MUI_FINISHPAGE_TEXT "$(RulesFinish)"
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "SimpChinese"
!insertmacro MUI_LANGUAGE "English"

LangString Running ${LANG_SIMPCHINESE} "请先关闭 PTCG Lab 和其他安装程序，然后重试。"
LangString Running ${LANG_ENGLISH} "Close PTCG Lab and any other setup window, then try again."
LangString WrongPlatform ${LANG_SIMPCHINESE} "此程序需要 64 位 Windows。"
LangString WrongPlatform ${LANG_ENGLISH} "This application requires 64-bit Windows."
LangString Recovery ${LANG_SIMPCHINESE} "资料目录有未完成的维护事务。请完成恢复后重试。"
LangString Recovery ${LANG_ENGLISH} "Complete the pending profile maintenance before installing."
LangString FilesError ${LANG_SIMPCHINESE} "无法切换程序文件。用户资料已保留。"
LangString FilesError ${LANG_ENGLISH} "Setup could not replace the application files. Your profile has been retained."
LangString RulesFinish ${LANG_SIMPCHINESE} "安装完成。若已有旧的本机对战规则，请关闭 PTCG Lab，再双击安装目录中的 Update Local Rules.cmd。$\r$\n$INSTDIR"
LangString RulesFinish ${LANG_ENGLISH} "Installation is complete. To update an existing local rule release, close PTCG Lab and run Update Local Rules.cmd from the installation folder.$\r$\n$INSTDIR"

Var Previous
Var Stage
Var Cleanup

!macro Fail message
  MessageBox MB_OK|MB_ICONSTOP "${message}" /SD IDOK
  SetErrorLevel 1
  Abort
!macroend

; Only missing paths and ordinary directories may take part in the transaction.
!macro CheckDirectory path
  System::Call 'kernel32::GetFileAttributesW(w "${path}") i .r0 ?e'
  Pop $1
  ${If} $0 = -1
    ${If} $1 <> 2
    ${AndIf} $1 <> 3
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
  ${Else}
    IntOp $0 $0 & 0x410
    ${If} $0 <> 0x10
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
  ${EndIf}
!macroend

; Match the app's no-sharing locks. Existing handles remain open until Setup exits.
!macro AcquireProfileLock path
  System::Call 'kernel32::CreateFileW(w "${path}", i 0x80000000, i 0, p 0, i 3, i 0x00200000, p 0) i .r0 ?e'
  Pop $1
  ${If} $0 = -1
    ${If} $1 <> 2
    ${AndIf} $1 <> 3
      !insertmacro Fail "$(Running)"
    ${EndIf}
  ${EndIf}
!macroend

!macro BeginOperation
  System::Call 'kernel32::CreateMutexW(p 0, i 0, w "Local\org.ptcglab.desktop.preview.setup") i .r0 ?e'
  Pop $1
  ${If} $0 = 0
  ${OrIf} $1 = 183
    !insertmacro Fail "$(Running)"
  ${EndIf}
  !insertmacro CheckDirectory "$LOCALAPPDATA\Programs"
  !insertmacro CheckDirectory "${PROFILE}"
  !insertmacro AcquireProfileLock "${PROFILE}\shell.lock"
  !insertmacro AcquireProfileLock "${PROFILE}\native.lock"
!macroend

!macro CheckInstalled path
  !insertmacro CheckDirectory "${path}"
  ${IfNot} ${FileExists} "${path}\ptcg-setup.marker"
    !insertmacro Fail "$(FilesError)"
  ${EndIf}
!macroend

!macro CleanPrevious
  !insertmacro CheckInstalled "$Previous"
  IfFileExists "$Previous\Uninstall.exe" 0 remove_previous_marker
    ClearErrors
    ExecWait '"$Previous\Uninstall.exe" /S /BACKUP _?=$Previous' $2
    ${If} ${Errors}
    ${OrIf} $2 <> 0
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
    Delete "$Previous\Uninstall.exe"
    ${If} ${FileExists} "$Previous\Uninstall.exe"
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
  remove_previous_marker:
    Delete "$Previous\ptcg-setup.marker"
    ClearErrors
    RMDir "$Previous"
    ${If} ${Errors}
      FileOpen $0 "$Previous\ptcg-setup.marker" w
      FileClose $0
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
!macroend

!include "${UNINSTALL_FILES}"

Function .onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $INSTDIR "${INSTALLROOT}"
  StrCpy $Previous "$INSTDIR.previous"
  ${IfNot} ${RunningX64}
    !insertmacro Fail "$(WrongPlatform)"
  ${EndIf}
FunctionEnd

Section "PTCG Lab"
  !insertmacro BeginOperation
  ${If} ${FileExists} "${PROFILE}\.desktop-maintenance.json"
    !insertmacro Fail "$(Recovery)"
  ${EndIf}
  !insertmacro CheckDirectory "$INSTDIR"
  !insertmacro CheckDirectory "$Previous"
  SetOutPath "$LOCALAPPDATA\Programs"

  ${If} ${FileExists} "$Previous\*"
    ${If} ${FileExists} "$INSTDIR\*"
      !insertmacro CheckInstalled "$INSTDIR"
      !insertmacro CleanPrevious
    ${Else}
      !insertmacro CheckInstalled "$Previous"
      ClearErrors
      Rename "$Previous" "$INSTDIR"
      IfErrors failed
    ${EndIf}
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\*"
    !insertmacro CheckInstalled "$INSTDIR"
  ${EndIf}

  ClearErrors
  GetTempFileName $Stage "$LOCALAPPDATA\Programs"
  IfErrors failed
  Delete "$Stage"
  SetOutPath "$Stage"
  IfErrors failed
  !insertmacro CheckDirectory "$Stage"
  SetOverwrite on
  File /r "${APP_FILES}/*"
  IfErrors failed
  WriteUninstaller "$Stage\Uninstall.exe"
  IfErrors failed
  SetOutPath "$LOCALAPPDATA\Programs"
  IfErrors failed

  ${If} ${FileExists} "$INSTDIR\*"
    Rename "$INSTDIR" "$Previous"
    IfErrors failed
  ${EndIf}
  ClearErrors
  Rename "$Stage" "$INSTDIR"
  ${If} ${Errors}
    ${If} ${FileExists} "$Previous\ptcg-setup.marker"
      Rename "$Previous" "$INSTDIR"
    ${EndIf}
    Goto failed
  ${EndIf}

  StrCpy $Stage ""
  SetOutPath "$INSTDIR"
  CreateShortCut "$SMPROGRAMS\${PRODUCT}.lnk" "$INSTDIR\${APP}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayName" "${PRODUCT}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALLKEY}" "DisplayIcon" "$INSTDIR\${APP}"
  WriteRegStr HKCU "${UNINSTALLKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegDWORD HKCU "${UNINSTALLKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALLKEY}" "NoRepair" 1
  IfErrors failed
  Goto installed

  failed:
    ${If} $Stage != ""
    ${AndIf} ${FileExists} "$Stage\*"
      SetOutPath "$LOCALAPPDATA\Programs"
      !insertmacro CheckDirectory "$Stage"
      !insertmacro CheckProgramDirectories "$Stage"
      !insertmacro RemoveProgramFiles "$Stage"
      Delete "$Stage\ptcg-setup.marker"
      Delete "$Stage\Uninstall.exe"
      RMDir "$Stage"
    ${EndIf}
    !insertmacro Fail "$(FilesError)"
  installed:
SectionEnd

Function un.onInit
  SetShellVarContext current
  SetRegView 64
  StrCpy $Previous "${INSTALLROOT}.previous"
  ${GetParameters} $0
  ${GetOptions} $0 "/BACKUP" $Cleanup
  ${If} ${Errors}
    StrCpy $Cleanup 0
    ${If} $INSTDIR != "${INSTALLROOT}"
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
  ${Else}
    StrCpy $Cleanup 1
    ${If} $INSTDIR != "$Previous"
      !insertmacro Fail "$(FilesError)"
    ${EndIf}
  ${EndIf}
FunctionEnd

Section "Uninstall"
  ${If} $Cleanup = 0
    !insertmacro BeginOperation
    ${If} ${FileExists} "$Previous\*"
      !insertmacro CleanPrevious
    ${EndIf}
  ${EndIf}
  !insertmacro CheckInstalled "$INSTDIR"
  SetOutPath "$LOCALAPPDATA\Programs"
  !insertmacro CheckProgramDirectories "$INSTDIR"
  !insertmacro RemoveProgramFiles "$INSTDIR"
  ${If} ${Errors}
    !insertmacro Fail "$(FilesError)"
  ${EndIf}
  ${If} $Cleanup = 0
    Delete "$SMPROGRAMS\${PRODUCT}.lnk"
    DeleteRegKey HKCU "${UNINSTALLKEY}"
    Delete "$INSTDIR\ptcg-setup.marker"
    Delete "$INSTDIR\Uninstall.exe"
    RMDir "$INSTDIR"
  ${EndIf}
SectionEnd
