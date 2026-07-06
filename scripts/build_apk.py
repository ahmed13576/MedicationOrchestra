import re
import sys
import subprocess
import os
import shutil
from datetime import datetime

def main():
    # Paths relative to the script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    workspace_root = os.path.dirname(script_dir)
    pubspec_path = os.path.join(workspace_root, 'medication_orchestra', 'pubspec.yaml')
    changelog_path = os.path.join(workspace_root, 'medication_orchestra', 'CHANGELOG.md')
    
    if not os.path.exists(pubspec_path):
        print(f"Error: pubspec.yaml not found at {pubspec_path}")
        sys.exit(1)
        
    # Read pubspec.yaml
    with open(pubspec_path, 'r', encoding='utf-8') as f:
        content = f.read()
        
    # Find version line (e.g. version: 1.0.0+1)
    version_match = re.search(r'^version:\s*(\d+\.\d+\.\d+)\+(\d+)', content, re.MULTILINE)
    if not version_match:
        print("Error: Could not find version block in pubspec.yaml")
        sys.exit(1)
        
    version_str, build_num_str = version_match.groups()
    current_build_num = int(build_num_str)
    new_build_num = current_build_num + 1
    new_version_line = f"version: {version_str}+{new_build_num}"
    
    print(f"Current version: {version_str}+{current_build_num}")
    print(f"Target version: {version_str}+{new_build_num}")
    
    # Prompt for changelog message
    try:
        changelog_message = input("Enter release notes/changelog message for this build: ").strip()
    except KeyboardInterrupt:
        print("\nBuild cancelled.")
        sys.exit(1)
        
    if not changelog_message:
        print("Error: Changelog message cannot be empty.")
        sys.exit(1)
        
    # Update pubspec.yaml content
    updated_content = re.sub(
        r'^version:\s*\d+\.\d+\.\d+\+\d+',
        new_version_line,
        content,
        flags=re.MULTILINE
    )
    
    # Write updated pubspec.yaml
    with open(pubspec_path, 'w', encoding='utf-8') as f:
        f.write(updated_content)
        
    # Update CHANGELOG.md
    date_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    changelog_entry = f"\n## [{version_str}+{new_build_num}] - {date_str}\n- {changelog_message}\n"
    
    mode = 'a' if os.path.exists(changelog_path) else 'w'
    with open(changelog_path, mode, encoding='utf-8') as f:
        f.write(changelog_entry)
        
    print(f"Updated pubspec.yaml to version {version_str}+{new_build_num}")
    print(f"Appended release notes to CHANGELOG.md")
    
    # Run flutter build apk
    cmd = [
        "flutter", "build", "apk",
        "--no-tree-shake-icons",
        "--dart-define=API_BASE_URL=https://medication-orchestra-backend-nhyktiopwq-uc.a.run.app"
    ]
    print(f"Running: {' '.join(cmd)}")
    
    # Run the build command inside the medication_orchestra folder
    working_dir = os.path.dirname(pubspec_path)
    # Using shell=True for windows compatibility
    result = subprocess.run(cmd, cwd=working_dir, shell=True)
    
    if result.returncode == 0:
        print("APK Build completed successfully!")
        
        # Rename the output to include version number
        apk_dir = os.path.join(working_dir, 'build', 'app', 'outputs', 'flutter-apk')
        original_apk = os.path.join(apk_dir, 'app-release.apk')
        new_apk_name = f"app-release_v{version_str}_{new_build_num}.apk"
        new_apk_path = os.path.join(apk_dir, new_apk_name)
        
        if os.path.exists(original_apk):
            try:
                shutil.copy2(original_apk, new_apk_path)
                print(f"Versioned APK successfully copied to: {new_apk_path}")
            except Exception as e:
                print(f"Error copying versioned APK file: {e}")
        else:
            print("Warning: Could not find build/app/outputs/flutter-apk/app-release.apk to create versioned copy.")
    else:
        print(f"APK Build failed with exit code: {result.returncode}")
        # Rollback version bump in pubspec.yaml
        with open(pubspec_path, 'w', encoding='utf-8') as f:
            f.write(content)
        print("Rolled back pubspec.yaml version bump.")
        
        # Also clean up the changelog if possible
        if os.path.exists(changelog_path):
            with open(changelog_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            # If the last lines match our entry, remove them
            # (Just a simple rollback, not strictly necessary but clean)
            entry_line_count = len(changelog_entry.strip().split('\n')) + 1
            if len(lines) >= entry_line_count:
                with open(changelog_path, 'w', encoding='utf-8') as f:
                    f.writelines(lines[:-entry_line_count])
            print("Rolled back CHANGELOG.md entry.")
            
        sys.exit(result.returncode)

if __name__ == '__main__':
    main()
