-- FrameDoctor droplet: drop clips on the app (or double-click to choose them).
-- Built into FrameDoctor.app by mac/build_app.sh; the engine lives in
-- Contents/Resources/framedoctor/framedoctor.

property appName : "FrameDoctor"

on run
	set theFiles to choose file with prompt "Choose clips to check for dropped and repeated frames:" with multiple selections allowed
	processFiles(theFiles)
end run

on open theFiles
	processFiles(theFiles)
end open

on processFiles(theFiles)
	set n to count of theFiles
	set choice to button returned of (display dialog "Check " & n & " clip(s) for dropped and repeated frames." & return & return & ¬
		"Report only — writes a CSV listing the problem frames." & return & ¬
		"Fix — also writes a repaired <name>_fixed.mov (ProRes 422 HQ) next to each clip. Originals are never changed." buttons {"Cancel", "Report only", "Fix"} default button "Fix" cancel button "Cancel" with title appName)
	set sens to button returned of (display dialog "Detection sensitivity" & return & return & ¬
		"Normal — fewest false hits." & return & ¬
		"Sensitive — catches subtle drops and slow openings." & return & ¬
		"Extra — catches the faintest; check the report for false hits." buttons {"Normal", "Sensitive", "Extra"} default button "Normal" with title appName)

	set binPath to (POSIX path of (path to me)) & "Contents/Resources/framedoctor/framedoctor"
	set summary to ""
	set lastFolder to missing value

	repeat with i from 1 to n
		set f to item i of theFiles
		set p to POSIX path of f
		set fname to do shell script "basename " & quoted form of p
		set lastFolder to do shell script "dirname " & quoted form of p
		set logFile to do shell script "mktemp -t framedoctor"

		set args to quoted form of p & " --progress"
		if choice is "Fix" then set args to args & " --fix"
		if sens is "Sensitive" then set args to args & " --sensitivity sensitive"
		if sens is "Extra" then set args to args & " --sensitivity extra"

		set pid to do shell script "( " & quoted form of binPath & " " & args & "; echo \"EXIT $?\" ) > " & quoted form of logFile & " 2>&1 & echo $!"

		set progress total steps to 100
		set progress completed steps to 0
		set progress description to "Clip " & i & " of " & n & ": " & fname
		set progress additional description to "Starting…"

		try
			repeat
				delay 0.4
				set lastProg to do shell script "grep '^PROGRESS' " & quoted form of logFile & " | tail -n 1 || true"
				if lastProg is not "" then
					set AppleScript's text item delimiters to " "
					set parts to text items of lastProg
					set AppleScript's text item delimiters to ""
					set phase to item 2 of parts
					set pct to (item 3 of parts) as integer
					if choice is "Fix" then
						if phase is "analysing" then
							set overall to pct div 2
						else
							set overall to 50 + (pct div 2)
						end if
					else
						set overall to pct
					end if
					set progress completed steps to overall
					set progress additional description to phase & "… " & pct & "%"
				end if
				set finished to do shell script "grep -c '^EXIT' " & quoted form of logFile & " || true"
				if finished is not "0" then exit repeat
			end repeat
		on error errMsg number errNum
			do shell script "pkill -P " & pid & " ; kill " & pid & " 2>/dev/null; true"
			if errNum is -128 then error number -128
			error errMsg number errNum
		end try

		set exitLine to do shell script "grep '^EXIT' " & quoted form of logFile & " | tail -n 1"
		if exitLine is "EXIT 0" then
			set found to do shell script "grep '^Found' " & quoted form of logFile & " | sed 's/ Report:.*//' || true"
			set summary to summary & "✓ " & fname & return & "    " & found & return
		else
			set errText to do shell script "grep -v -e '^PROGRESS' -e '^EXIT' " & quoted form of logFile & " | tail -n 3"
			set summary to summary & "✗ " & fname & return & "    " & errText & return
		end if
		do shell script "rm -f " & quoted form of logFile
	end repeat

	set progress completed steps to 100
	set resultNote to "Reports (_report.csv)"
	if choice is "Fix" then set resultNote to resultNote & " and fixed clips (_fixed.mov)"
	set btn to button returned of (display dialog summary & return & resultNote & " are next to the originals." buttons {"Show in Finder", "Done"} default button "Done" with title appName)
	if btn is "Show in Finder" and lastFolder is not missing value then
		do shell script "open " & quoted form of lastFolder
	end if
end processFiles
