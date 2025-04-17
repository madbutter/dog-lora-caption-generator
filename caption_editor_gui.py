import os
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from PIL import Image, ImageTk
import zipfile
from typing import Dict, Tuple
import openai
import base64
from tqdm import tqdm
import logging
import threading
import queue
import time
import json
from datetime import datetime

class CaptionEditorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Dog Painting Caption Editor")
        
        # Configuration
        self.images_dir = "images"
        self.captions_dir = "captions"
        self.thumbnail_size = (200, 200)
        self.progress_file = "caption_progress.json"
        
        # Create a style for the filename entry
        style = ttk.Style()
        style.configure(
            'Filename.TEntry',
            fieldbackground='#f0f0f0',  # Light gray background
            borderwidth=0,              # No border
        )
        
        # Queue for thread communication
        self.caption_queue = queue.Queue()
        
        # Control flags
        self.processing = False
        self.should_stop = False
        
        # Initialize OpenAI
        self.openai_key = os.getenv("OPENAI_API_KEY")
        if self.openai_key:
            openai.api_key = self.openai_key
        
        # Store references to avoid garbage collection
        self.thumbnail_refs: Dict[str, ImageTk.PhotoImage] = {}
        self.caption_widgets: Dict[str, scrolledtext.ScrolledText] = {}
        self.caption_frames: Dict[str, tk.Frame] = {}
        self.original_captions: Dict[str, str] = {}
        self.current_active_frame = None
        
        # Progress tracking
        self.completed_files = self.load_progress()
        
        # Bind window close event
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        # Create main frame with scrollbar
        self.main_frame = ttk.Frame(root)
        self.main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Create canvas and scrollbar
        self.canvas = tk.Canvas(self.main_frame)
        self.scrollbar = ttk.Scrollbar(self.main_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)
        
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        
        # Pack scrollbar and canvas
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Add mousewheel scrolling
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        
        # Remove ttk styles as we'll use tk.Frame instead
        self.ACTIVE_BORDER_COLOR = "#2ecc71"  # Bright green
        self.INACTIVE_BORDER_COLOR = "#e0e0e0"  # Light gray
        self.BORDER_WIDTH = 2
        
        # Create control panel
        self.create_control_panel()
        
        # Create progress bar and status label
        self.progress_frame = ttk.Frame(self.root)
        self.progress_frame.pack(fill=tk.X, padx=10, pady=5)
        
        self.status_label = ttk.Label(self.progress_frame, text="")
        self.status_label.pack(side=tk.LEFT, padx=(0, 10))
        
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(self.progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Load images and captions
        self.load_content()
        
        # Start checking for caption updates
        self.check_caption_queue()

    def on_closing(self):
        """Handle window close event"""
        if self.processing:
            if messagebox.askokcancel("Quit", "Caption generation is in progress. Do you want to stop and quit?"):
                self.should_stop = True
                self.root.after(1000, self.root.destroy)  # Give time for thread to clean up
        else:
            self.root.destroy()

    def create_control_panel(self):
        """Create the control panel with zip filename entry and buttons"""
        control_panel = ttk.Frame(self.root)
        control_panel.pack(fill=tk.X, padx=10, pady=(0, 10))
        
        # Left side - caption generation
        left_panel = ttk.Frame(control_panel)
        left_panel.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Add Generate and Clear All buttons
        self.generate_btn = ttk.Button(
            left_panel, 
            text="Generate All Captions", 
            command=lambda: self.generate_captions()
        )
        self.generate_btn.pack(side=tk.LEFT, padx=5)
        
        self.clear_btn = ttk.Button(
            left_panel,
            text="Clear All",
            command=self.clear_all_captions
        )
        self.clear_btn.pack(side=tk.LEFT, padx=5)
        
        # Right side - export controls
        right_panel = ttk.Frame(control_panel)
        right_panel.pack(side=tk.RIGHT, fill=tk.X)
        
        ttk.Label(right_panel, text="Export filename:").pack(side=tk.LEFT, padx=(0, 5))
        self.zip_filename = ttk.Entry(right_panel, width=30)
        self.zip_filename.pack(side=tk.LEFT, padx=(0, 10))
        self.zip_filename.insert(0, "dog_paintings_dataset.zip")
        
        ttk.Button(right_panel, text="Save Changes", command=self.save_changes).pack(side=tk.LEFT, padx=5)
        ttk.Button(right_panel, text="Export Zip", command=self.export_zip).pack(side=tk.LEFT, padx=5)

    def build_prompt(self):
        return (
            "Provide a raw caption with these elements in a flowing, comma-separated format:\n"
            "1. Dog breed\n"
            "2. Pose\n"
            "3. Fur details\n"
            "4. Painting style\n"
            "5. Setting\n"
            "6. View angle\n\n"
            "OUTPUT FORMAT:\n"
            "Write the caption directly with no prefixes or quotes. Start with the breed and end with the view angle.\n\n"
            "EXAMPLE OUTPUT:\n"
            "Golden Retriever sitting alertly, long flowing golden fur, painted in realistic style, in a garden setting, three-quarter view\n\n"
            "YOUR TURN - CAPTION ONLY:"
        )

    def set_active_frame(self, img_file):
        """Highlight the currently active frame and reset the previous one"""
        if self.current_active_frame:
            # Reset previous frame
            prev_frame = self.caption_frames.get(self.current_active_frame)
            if prev_frame:
                prev_frame.configure(
                    highlightcolor=self.INACTIVE_BORDER_COLOR,
                    highlightbackground=self.INACTIVE_BORDER_COLOR
                )
        
        # Set new active frame
        new_frame = self.caption_frames.get(img_file)
        if new_frame:
            new_frame.configure(
                highlightcolor=self.ACTIVE_BORDER_COLOR,
                highlightbackground=self.ACTIVE_BORDER_COLOR
            )
            # Ensure the active frame is visible
            new_frame.update_idletasks()
            self.canvas.yview_moveto(new_frame.winfo_y() / self.scrollable_frame.winfo_height())
        
        self.current_active_frame = img_file

    def check_caption_queue(self):
        """Check for completed captions and update the GUI"""
        try:
            while True:
                msg_type, data = self.caption_queue.get_nowait()
                
                if msg_type == "UPDATE_GUI":
                    # Update caption widget
                    img_file = data['img_file']
                    caption = data['caption']
                    if img_file in self.caption_widgets:
                        widget = self.caption_widgets[img_file]
                        widget.delete('1.0', tk.END)
                        widget.insert('1.0', caption)
                        self.original_captions[img_file] = caption
                        
                        # Update completed files and progress
                        self.completed_files = data['completed_files']
                        self.completed_images = len(self.completed_files)
                        progress = (self.completed_images / data['total']) * 100
                        
                        # Update progress display
                        self.progress_var.set(progress)
                        self.status_label.config(
                            text=f"Processing: {self.completed_images}/{data['total']}"
                        )
                        
                        # Highlight current frame
                        self.set_active_frame(img_file)
                        
                        # Force GUI updates
                        widget.update_idletasks()
                        self.status_label.update_idletasks()
                        self.progress_frame.update_idletasks()
                    continue
                    
                elif msg_type == "DONE":
                    print("Processing complete")
                    self.progress_frame.pack_forget()
                    self.processing = False
                    self.generate_btn.configure(state="normal")
                    if self.current_active_frame:
                        self.caption_frames[self.current_active_frame].configure(
                            highlightcolor=self.INACTIVE_BORDER_COLOR,
                            highlightbackground=self.INACTIVE_BORDER_COLOR
                        )
                        self.current_active_frame = None
                    messagebox.showinfo("Success", "Caption generation complete!")
                    return
                    
                elif msg_type == "ERROR":
                    messagebox.showerror("Error", data)
                    continue
                    
                elif msg_type == "STATUS":
                    self.status_label.config(text=data)
                    self.status_label.update_idletasks()
                    continue
                    
        except queue.Empty:
            pass
            
        # Schedule next check
        self.root.after(100, self.check_caption_queue)

    def load_progress(self) -> set:
        """Load progress from JSON file"""
        try:
            if os.path.exists(self.progress_file):
                with open(self.progress_file, 'r') as f:
                    data = json.load(f)
                    self.total_images = data.get('total_images', 0)
                    self.completed_images = data.get('completed_images', 0)
                    return set(data.get('completed_files', []))
            return set()
        except Exception as e:
            print(f"Error loading progress: {e}")
            return set()

    def save_progress(self):
        """Save progress to JSON file"""
        try:
            data = {
                'completed_files': list(self.completed_files),
                'last_updated': datetime.now().isoformat(),
                'total_images': self.total_images,
                'completed_images': len(self.completed_files)
            }
            with open(self.progress_file, 'w') as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Error saving progress: {e}")

    def generate_captions(self):
        """Start caption generation in a background thread"""
        if not self.openai_key:
            messagebox.showerror(
                "Error", 
                "OpenAI API key not found! Please set the OPENAI_API_KEY environment variable."
            )
            return
        
        if self.processing:
            return  # Already processing
            
        # Reset GUI state
        self.processing = True
        self.should_stop = False
        
        # Get sorted list of all images
        all_images = sorted(f for f in os.listdir(self.images_dir) 
                          if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')))
        self.total_images = len(all_images)
        
        # Start fresh
        print("\nStarting caption generation")
        self.completed_files.clear()
        self.completed_images = 0
        images_to_process = all_images.copy()
        
        # Clear all caption widgets
        for widget in self.caption_widgets.values():
            widget.delete('1.0', tk.END)
        self.original_captions.clear()
        
        # Show progress elements
        self.progress_frame.pack(fill=tk.X, padx=10, pady=5)
        self.progress_var.set(0)
        self.status_label.config(text=f"Processing: 0/{self.total_images}")
        
        # Disable generate button during processing
        self.generate_btn.configure(state="disabled")
        
        # Force GUI update
        self.root.update_idletasks()
        
        # Start processing thread
        thread = threading.Thread(
            target=self.process_images_thread, 
            args=(images_to_process, all_images)
        )
        thread.daemon = True
        thread.start()

    def process_images_thread(self, images_to_process, all_images):
        """Process images in a background thread"""
        try:
            total = len(all_images)
            
            for img_file in images_to_process:
                if self.should_stop:
                    print("\nProcessing stopped by user")
                    self.caption_queue.put(("STOPPED", None))
                    break
                
                try:
                    # Get global index (1-based) for this image
                    global_idx = all_images.index(img_file) + 1
                    
                    print(f"\nProcessing [{global_idx}/{total}] {img_file}")
                    img_path = os.path.join(self.images_dir, img_file)
                    out_path = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
                    
                    # Update status
                    status_msg = f"Processing: {img_file} ({global_idx}/{total})"
                    self.caption_queue.put(("STATUS", status_msg))
                    
                    # Generate caption
                    caption = self.caption_image(img_path, global_idx, total)
                    if not caption or len(caption.strip()) == 0:
                        raise ValueError("Empty caption received")
                    
                    # Save to file
                    with open(out_path, 'w') as f:
                        f.write(caption)
                    print(f"💾 Saved caption for [{global_idx}/{total}] {img_file}")
                    
                    # Update GUI and progress tracking
                    self.caption_queue.put(("UPDATE_GUI", {
                        'img_file': img_file,
                        'caption': caption,
                        'completed_files': self.completed_files | {img_file},
                        'total': total
                    }))
                    
                    # Small delay between images
                    time.sleep(0.5)
                    
                except Exception as e:
                    error_msg = f"❌ Failed to process {img_file}: {str(e)}"
                    print(error_msg)
                    self.caption_queue.put(("ERROR", error_msg))
                    if "quota" in str(e).lower():
                        self.should_stop = True
                        break
                    continue
                    
        finally:
            print("\nProcessing thread finished")
            self.processing = False
            if not self.should_stop:
                self.caption_queue.put(("DONE", None))

    def caption_image(self, image_path, idx, total):
        """Generate caption for a single image using GPT-4 Vision"""
        # Set up logging for this session
        logging.basicConfig(
            filename='caption_debug.log',
            level=logging.DEBUG,
            format='%(asctime)s - %(message)s',
            filemode='a'  # Append mode instead of write
        )
        
        filename = os.path.basename(image_path)
        logging.debug(f"\n[{idx}/{total}] Starting processing of: {filename}")
        
        with open(image_path, "rb") as img_file:
            image_bytes = img_file.read()
            base64_image = base64.b64encode(image_bytes).decode('utf-8')

        logging.debug(f"[{idx}/{total}] Sending request to OpenAI API for {filename}")
        print(f"🔄 Sending API request for [{idx}/{total}] {filename}...")
        
        try:
            response = openai.chat.completions.create(
                model="gpt-4o",  # Updated to recommended replacement model for vision capabilities
                messages=[
                    {
                        "role": "system", 
                        "content": (
                            "You are a caption generator that follows instructions precisely. "
                            "Output ONLY the raw caption text. "
                            "DO NOT add any prefixes, quotes, or formatting. "
                            "The output should be exactly like this example: "
                            "Golden Retriever sitting alertly, long flowing golden fur, painted in realistic style, in a garden setting, three-quarter view"
                        )
                    },
                    {
                        "role": "user", 
                        "content": [
                            {"type": "text", "text": self.build_prompt()},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_image}",
                                    "detail": "low"  # Use low detail to reduce tokens
                                }
                            }
                        ]
                    }
                ],
                max_tokens=300,
            )

            caption = response.choices[0].message.content.strip()
            logging.debug(f"[{idx}/{total}] Received response for {filename}")
            logging.debug(f"[{idx}/{total}] Original caption: '{caption}'")
            print(f"📝 Got response for [{idx}/{total}] {filename}")
            
            # Remove all known prefix variations
            prefixes_to_remove = [
                "Caption for Image:", 
                "Caption for the Image:",
                "Caption for the image:",
                "Caption for The Image:",
                "Caption:", 
                "Description:", 
                "Generated Caption:", 
                "Image Caption:",
                "Caption for Art Training Dataset:",
                "Caption for Image Training:",
                "Final Caption:",
                "Suggested Caption:"
            ]
            
            # Convert to lowercase for comparison but keep original for replacement
            caption_lower = caption.lower()
            original_caption = caption
            
            logging.debug(f"[{idx}/{total}] Looking for prefixes in: '{caption_lower}'")
            for prefix in prefixes_to_remove:
                prefix_lower = prefix.lower()
                logging.debug(f"[{idx}/{total}] Checking prefix: '{prefix_lower}'")
                if caption_lower.startswith(prefix_lower):
                    logging.debug(f"[{idx}/{total}] Found matching prefix: '{prefix}'")
                    # Use the length of the matching text from the original
                    prefix_length = len(caption_lower[:len(prefix_lower)].strip())
                    caption = original_caption[prefix_length:].strip()
                    print(f"🔍 Removed prefix in [{idx}/{total}] {filename}")
                    break
            
            logging.debug(f"[{idx}/{total}] After prefix removal: '{caption}'")
            
            # Remove surrounding quotes (both single and double)
            if (caption.startswith('"') and caption.endswith('"')) or \
               (caption.startswith("'") and caption.endswith("'")):
                caption = caption[1:-1].strip()
                logging.debug(f"[{idx}/{total}] Removed surrounding quotes")
                print(f"✂️ Removed quotes in [{idx}/{total}] {filename}")
            
            logging.debug(f"[{idx}/{total}] Final caption for {filename}: '{caption}'")
            print(f"✨ Finalized caption for [{idx}/{total}] {filename}")
            
            return caption.strip()
            
        except Exception as e:
            logging.error(f"[{idx}/{total}] Error processing {filename}: {str(e)}")
            raise

    def load_content(self):
        """Load and display images with their captions"""
        # Get sorted list of images
        self.all_images = [f for f in os.listdir(self.images_dir) 
                          if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))]
        self.all_images.sort()
        self.total_images = len(self.all_images)
        
        for idx, img_file in enumerate(self.all_images):
            # Create frame for this image-caption pair using tk.Frame
            pair_frame = tk.Frame(
                self.scrollable_frame,
                borderwidth=self.BORDER_WIDTH,
                relief="solid",
                highlightthickness=self.BORDER_WIDTH,
                highlightcolor=self.INACTIVE_BORDER_COLOR,
                highlightbackground=self.INACTIVE_BORDER_COLOR
            )
            pair_frame.pack(fill=tk.X, padx=5, pady=5)
            self.caption_frames[img_file] = pair_frame
            
            # Left side: Image and caption
            left_frame = tk.Frame(pair_frame)
            left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            
            # Load and display thumbnail
            img_path = os.path.join(self.images_dir, img_file)
            try:
                image = Image.open(img_path)
                image.thumbnail(self.thumbnail_size)
                photo = ImageTk.PhotoImage(image)
                self.thumbnail_refs[img_file] = photo
                
                label = ttk.Label(left_frame, image=photo)
                label.pack(side=tk.LEFT, padx=(0, 10))
                
                # Create caption container frame
                caption_container = tk.Frame(left_frame)
                caption_container.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
                
                # Add filename display using a Label
                filename_label = ttk.Label(
                    caption_container,
                    text=img_file,
                    background='#f0f0f0',
                    padding=(5, 2)  # Add some padding for better appearance
                )
                filename_label.pack(fill=tk.X, pady=(0, 2))
                
                # Create caption widget with slightly reduced height
                caption_widget = scrolledtext.ScrolledText(caption_container, height=4, width=50)
                caption_widget.pack(fill=tk.BOTH, expand=True)
                
                # Load caption if it exists
                caption_file = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
                if os.path.exists(caption_file):
                    with open(caption_file, 'r') as f:
                        caption = f.read().strip()
                        caption_widget.insert('1.0', caption)
                        self.original_captions[img_file] = caption
                        self.completed_files.add(img_file)
                
                self.caption_widgets[img_file] = caption_widget
                
                # Right side: Buttons
                button_frame = tk.Frame(pair_frame)
                button_frame.pack(side=tk.RIGHT, padx=5)
                
                # Add Clear and Generate buttons for this row
                clear_btn = ttk.Button(
                    button_frame, 
                    text="Clear",
                    command=lambda i=idx: self.clear_single_caption(i)
                )
                clear_btn.pack(side=tk.RIGHT, padx=2)
                
                generate_btn = ttk.Button(
                    button_frame, 
                    text="Generate",
                    command=lambda i=idx: self.generate_single_caption(i)
                )
                generate_btn.pack(side=tk.RIGHT, padx=2)
                
            except Exception as e:
                print(f"Error loading {img_file}: {e}")

    def clear_single_caption(self, idx):
        """Clear caption for a single image"""
        try:
            # Get image file from index
            img_file = self.all_images[idx]
            display_idx = idx + 1  # 1-based index for display
            
            # Confirm with user
            if not messagebox.askokcancel("Confirm", f"Clear caption for image {display_idx}?"):
                return
                
            # Clear caption widget
            if img_file in self.caption_widgets:
                widget = self.caption_widgets[img_file]
                widget.delete('1.0', tk.END)
                widget.update_idletasks()
            
            # Delete caption file
            caption_file = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
            if os.path.exists(caption_file):
                os.remove(caption_file)
            
            # Update tracking
            if img_file in self.completed_files:
                self.completed_files.remove(img_file)
            if img_file in self.original_captions:
                del self.original_captions[img_file]
            
            print(f"✨ Cleared caption for image {display_idx}: {img_file}")
            
        except Exception as e:
            error_msg = f"Error clearing caption for image {idx + 1}: {e}"
            print(f"❌ {error_msg}")
            messagebox.showerror("Error", error_msg)

    def generate_single_caption(self, idx):
        """Generate caption for a single image"""
        if not self.openai_key:
            messagebox.showerror(
                "Error", 
                "OpenAI API key not found! Please set the OPENAI_API_KEY environment variable."
            )
            return
        
        if self.processing:
            messagebox.showwarning(
                "Warning", 
                "Caption generation already in progress. Please wait or use Clear All to stop."
            )
            return
            
        try:
            # Get image file from index
            img_file = self.all_images[idx]
            display_idx = idx + 1  # 1-based index for display
            
            # Reset GUI state for this row
            self.processing = True
            self.should_stop = False
            
            # Show progress elements
            self.progress_frame.pack(fill=tk.X, padx=10, pady=5)
            self.progress_var.set(0)
            self.status_label.config(text=f"Processing image {display_idx}/{self.total_images}")
            
            # Disable generate buttons during processing
            self.generate_btn.configure(state="disabled")
            
            # Force GUI update
            self.root.update_idletasks()
            
            # Start processing thread for single image
            thread = threading.Thread(
                target=self.process_single_image_thread,
                args=(idx,)
            )
            thread.daemon = True
            thread.start()
            
        except Exception as e:
            error_msg = f"Error starting caption generation for image {idx + 1}: {e}"
            print(f"❌ {error_msg}")
            messagebox.showerror("Error", error_msg)
            self.processing = False
            self.generate_btn.configure(state="normal")

    def process_single_image_thread(self, idx):
        """Process a single image in a background thread"""
        try:
            # Get image file from index
            img_file = self.all_images[idx]
            display_idx = idx + 1  # 1-based index for display
            
            print(f"\nProcessing [{display_idx}/{self.total_images}] {img_file}")
            img_path = os.path.join(self.images_dir, img_file)
            out_path = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
            
            # Update status
            status_msg = f"Processing: {img_file} ({display_idx}/{self.total_images})"
            self.caption_queue.put(("STATUS", status_msg))
            
            # Generate caption
            caption = self.caption_image(img_path, display_idx, self.total_images)
            if not caption or len(caption.strip()) == 0:
                raise ValueError("Empty caption received")
            
            # Save to file
            with open(out_path, 'w') as f:
                f.write(caption)
            print(f"💾 Saved caption for [{display_idx}/{self.total_images}] {img_file}")
            
            # Update GUI
            self.caption_queue.put(("UPDATE_GUI", {
                'img_file': img_file,
                'caption': caption,
                'completed_files': self.completed_files | {img_file},
                'total': self.total_images
            }))
            
        except Exception as e:
            error_msg = f"❌ Failed to process image {display_idx}: {str(e)}"
            print(error_msg)
            self.caption_queue.put(("ERROR", error_msg))
            
        finally:
            print("\nSingle image processing finished")
            self.processing = False
            self.caption_queue.put(("DONE", None))

    def _on_mousewheel(self, event):
        """Handle mousewheel scrolling"""
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def save_changes(self):
        """Save edited captions back to files"""
        changes_made = False
        for img_file, widget in self.caption_widgets.items():
            current_text = widget.get('1.0', tk.END).strip()
            if img_file in self.original_captions and current_text != self.original_captions[img_file]:
                caption_file = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
                with open(caption_file, 'w') as f:
                    f.write(current_text)
                self.original_captions[img_file] = current_text
                changes_made = True
        
        if changes_made:
            messagebox.showinfo("Success", "Changes saved successfully!")
        else:
            messagebox.showinfo("Info", "No changes to save")

    def export_zip(self):
        """Create a zip file containing all images and their captions"""
        zip_name = self.zip_filename.get()
        if not zip_name.endswith('.zip'):
            zip_name += '.zip'
        
        try:
            with zipfile.ZipFile(zip_name, 'w') as zipf:
                # Add images
                for img_file in os.listdir(self.images_dir):
                    if img_file.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        img_path = os.path.join(self.images_dir, img_file)
                        zipf.write(img_path, img_file)
                        
                        # Add corresponding caption
                        caption_file = os.path.join(self.captions_dir, 
                                                  os.path.splitext(img_file)[0] + ".txt")
                        if os.path.exists(caption_file):
                            zipf.write(caption_file, 
                                     os.path.splitext(img_file)[0] + ".txt")
            
            messagebox.showinfo("Success", f"Dataset exported to {zip_name}")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create zip file: {e}")

    def clear_all_captions(self):
        """Clear all captions and reset the GUI"""
        if self.processing:
            if not messagebox.askokcancel("Confirm", "Caption generation is in progress. Stop processing and clear all captions?"):
                return
            self.should_stop = True
        
        if not messagebox.askokcancel("Confirm", "This will delete all caption files and clear all fields. Are you sure?"):
            return
        
        try:
            print("\n🧹 Clearing all captions and resetting interface...")
            
            # Stop any ongoing processing
            self.should_stop = True
            self.processing = False
            
            # Reset progress tracking
            self.completed_files.clear()
            self.completed_images = 0
            
            # Clear progress file
            if os.path.exists(self.progress_file):
                try:
                    os.remove(self.progress_file)
                    print("✓ Deleted progress file")
                except Exception as e:
                    print(f"❌ Error deleting progress file: {e}")
            
            # Clear all caption files and widgets
            caption_files_deleted = 0
            for img_file in self.all_images:
                # Clear caption file
                caption_file = os.path.join(self.captions_dir, os.path.splitext(img_file)[0] + ".txt")
                if os.path.exists(caption_file):
                    try:
                        os.remove(caption_file)
                        caption_files_deleted += 1
                    except Exception as e:
                        print(f"❌ Error deleting {caption_file}: {e}")
                
                # Clear caption widget
                if img_file in self.caption_widgets:
                    widget = self.caption_widgets[img_file]
                    widget.delete('1.0', tk.END)
                    widget.update_idletasks()
            
            print(f"✓ Deleted {caption_files_deleted} caption files")
            
            # Clear stored captions
            self.original_captions.clear()
            
            # Reset progress elements
            self.progress_var.set(0)
            self.status_label.config(text="")
            
            # Hide progress frame if visible
            if self.progress_frame.winfo_ismapped():
                self.progress_frame.pack_forget()
            
            # Reset active frame highlighting
            if self.current_active_frame:
                self.caption_frames[self.current_active_frame].configure(
                    highlightcolor=self.INACTIVE_BORDER_COLOR,
                    highlightbackground=self.INACTIVE_BORDER_COLOR
                )
                self.current_active_frame = None
            
            # Reset button
            self.generate_btn.configure(state="normal")
            
            # Force GUI updates
            self.root.update_idletasks()
            
            print("✨ Interface reset complete")
            messagebox.showinfo("Success", "All captions have been cleared and interface has been reset!")
            
        except Exception as e:
            error_msg = f"Error clearing captions: {e}"
            print(f"❌ {error_msg}")
            messagebox.showerror("Error", error_msg)

if __name__ == "__main__":
    root = tk.Tk()
    app = CaptionEditorApp(root)
    root.mainloop() 