import yaml
import os
import shutil

from freeproxy import download_freeproxy

def load_config(config_path):
    with open(config_path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)

def merge_files(merge_list, base_dir):
    if not merge_list:
        return

    configs_dir = os.path.join(base_dir, 'configs')
    print(f"[Merge] Starting file merging in {configs_dir}")

    for entry in merge_list:
        if not isinstance(entry, dict):
            continue

        for target_file, source_files in entry.items():
            target_path = os.path.join(configs_dir, target_file)
            print(f"[Merge] Merging {source_files} into {target_file}")

            unique_lines = []
            seen = set()
            for src in source_files:
                src_path = os.path.join(configs_dir, src)
                if os.path.exists(src_path):
                    with open(src_path, 'r', encoding='utf-8') as f:
                        for line in f:
                            clean_line = line.strip()
                            if clean_line and clean_line not in seen:
                                unique_lines.append(clean_line)
                                seen.add(clean_line)
                else:
                    print(f"[Merge] Warning: Source file {src} not found.")

            if unique_lines:
                with open(target_path, 'w', encoding='utf-8') as f:
                    f.write('\n'.join(unique_lines) + '\n')
                print(f"[Merge] Successfully created {target_file} (Total: {len(unique_lines)} unique lines)")
            else:
                print(f"[Merge] Skip {target_file}: No content found in source files.")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # Clean output directory
    configs_dir = os.path.join(base_dir, 'configs')
    if os.path.exists(configs_dir):
        print(f"[Main] Cleaning output directory: {configs_dir}")
        shutil.rmtree(configs_dir)
    os.makedirs(configs_dir, exist_ok=True)

    config = load_config(os.path.join(base_dir, 'config.yml'))

    freeproxy_list = config.get('freeproxy_list', [])
    for fp_config in freeproxy_list:
        download_freeproxy(fp_config, base_dir)

    merge_list = config.get('merge_list', [])
    merge_files(merge_list, base_dir)

if __name__ == "__main__":
    main()
