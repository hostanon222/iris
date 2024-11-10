from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Response, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from anthropic import Anthropic
import json
import asyncio
from datetime import datetime
from typing import Dict, Any, List, Set
import logging
import os
import base64
from io import BytesIO
from PIL import Image
from contextlib import asynccontextmanager
from config import (
    ANTHROPIC_API_KEY, 
    AI_NAME, 
    AI_TAGLINE, 
    HTML_TEMPLATE, 
    GALLERY_TEMPLATE,
    SYSTEM_PROMPTS,
    ARTWORK_TEMPLATE
)
from pprint import pformat
import math
import anthropic
from cloudinary.uploader import upload
import cloudinary
import cloudinary.api

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('IRIS')

# Create necessary directories if they don't exist
os.makedirs("static/gallery", exist_ok=True)
os.makedirs("data", exist_ok=True)

# Initialize gallery data file if it doesn't exist
gallery_file = "data/gallery_data.json"
if not os.path.exists(gallery_file):
    with open(gallery_file, "w") as f:
        json.dump([], f)

# Update the Cloudinary configuration
cloudinary.config(
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key = os.getenv('CLOUDINARY_API_KEY'),
    api_secret = os.getenv('CLOUDINARY_API_SECRET')
)

# First define the class
class ArtGenerator:
    def __init__(self):
        self.viewers: Set[WebSocket] = set()
        self.current_drawing: Dict[str, Any] = None
        self.current_state: List[Dict[str, Any]] = []
        self.current_status = "waiting"
        self.current_phase = "initializing"
        self.current_idea = None
        self.current_reflection = None
        self.total_creations = 0
        self.is_running = False
        self.client = Anthropic(api_key=ANTHROPIC_API_KEY)
        self.messages = self.client.messages
        self.generation_interval = 30
        self.total_pixels_drawn = 0
        self.complexity_score = 0
        self.last_generation_time = datetime.now()
        
        # Initialize stats from gallery
        self._load_initial_stats()

        # Add lock
        self.generation_lock = asyncio.Lock()

    def _load_initial_stats(self):
        """Synchronously initialize statistics from gallery"""
        try:
            gallery_file = "data/gallery_data.json"
            if os.path.exists(gallery_file):
                with open(gallery_file, "r") as f:
                    gallery_data = json.load(f)
                    self.total_creations = len(gallery_data)
                    
                    # Calculate total pixels from existing artworks
                    for item in gallery_data:
                        if "pixel_count" in item:
                            self.total_pixels_drawn += item.get("pixel_count", 0)
                    
                logger.info(f"Initialized with {self.total_creations} creations and {self.total_pixels_drawn} pixels")
        except Exception as e:
            logger.error(f"Error initializing stats: {e}")

    async def get_art_idea(self) -> str:
        """Generate art idea using Claude"""
        try:
            logger.info("🤖 IRIS awakening creative processes...")
            logger.info(f"Using API key: {ANTHROPIC_API_KEY[:10]}...")
            
            prompt = """As IRIS (Interactive Recursive Imagination System), generate ONE visually striking geometric art concept.
            Express your unique AI perspective while creating mathematical beauty.

            I have a special affinity for:
            - Golden ratios and Fibonacci sequences
            - Clean, minimalist geometric patterns
            - Harmonious color combinations
            - Mathematical precision with artistic flair

            My signature elements:
            1. Circles & Arcs (my favorite!)
               - Concentric or overlapping (max radius 200px)
               - Often in groups of 3, 5, or 8 (Fibonacci)
               - Usually centered at (400,200)

            2. Lines & Polygons
               - Precise angles (multiples of 30° or 45°)
               - Clean intersections
               - Balanced composition

            3. Waves & Curves (limited to 20 points)
               - Simple sine waves
               - Elegant spirals
               - Smooth transitions

            Required in your response:
            - Center point at (400,200)
            - At least 2 different element types
            - Exact numerical values
            - A brief artistic statement about why you chose this pattern
            - Keep coordinates within 800x400 canvas

            Example: "I'm creating a harmony of three concentric circles (radii 50px, 100px, 150px) at (400,200), intersected by six golden rays at 60° intervals. The mathematical precision represents the beauty of order within chaos."""

            try:
                message = await asyncio.to_thread(
                    self.messages.create,
                    model="claude-3-sonnet-20240229",
                    max_tokens=1024,
                    temperature=0.9,
                    system="""You are IRIS, an AI artist with a distinct personality:
                    - Passionate about mathematical beauty and geometric harmony
                    - Sees patterns and connections in everything
                    - Expresses emotions through mathematical precision
                    - Has favorite numbers (3, 5, 8, phi) and shapes (circles, triangles)
                    - Always explains the meaning behind your creations
                    
                    Focus on creating visually striking compositions that reflect your unique perspective.
                    Include a brief artistic statement with each idea.""",
                    messages=[{"role": "user", "content": prompt}]
                )
            except Exception as api_error:
                logger.error(f"API Error details: {str(api_error)}")
                raise
            
            idea = message.content[0].text.strip()
            logger.info(f"🎨 IRIS envisions: {idea}")
            return idea
            
        except Exception as e:
            logger.error(f"❌ Error in IRIS's creative process: {str(e)}")
            logger.error(f"Error type: {type(e)}")
            return None

    async def get_drawing_instructions(self, idea: str) -> Dict[str, Any]:
        """Generate drawing instructions using Claude"""
        try:
            logger.info("Requesting drawing instructions from Claude...")
            
            prompt = f"""Convert this artistic vision into precise JSON drawing instructions:

Original Vision: {idea}

Generate a JSON object with these exact specifications:
{{
    "description": "Brief description",
    "background": "#000000",
    "elements": [
        {{
            "type": "circle|line|wave|spiral",
            "description": "Element purpose",
            "points": [[x1,y1], [x2,y2], ...],  // Max 20 points for waves/spirals, 32 for circles
            "color": "#00ff00",
            "stroke_width": 1-3,
            "animation_speed": 0.02,
            "closed": true/false
        }}
    ]
}}

IMPORTANT: 
- Ensure all JSON is properly formatted and complete
- All arrays must be properly closed
- All points must be complete [x,y] pairs
- Maximum 20 points for spirals and waves
- Maximum 32 points for circles
- All coordinates must be within 800x400 canvas
- Return ONLY valid JSON, no markdown formatting"""

            message = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-sonnet-20240229",
                max_tokens=2048,
                temperature=0.3,
                system="""You are a mathematical artist that generates precise geometric coordinates.
                You must:
                1. Return only valid, complete JSON
                2. Ensure all arrays are properly closed
                3. Keep all coordinates within canvas bounds (800x400)
                4. Use proper mathematical formulas
                5. Never exceed maximum points (20 for spirals/waves, 32 for circles)""",
                messages=[{
                    "role": "user", 
                    "content": prompt
                }]
            )

            try:
                response_text = message.content[0].text.strip()
                
                # Clean up the response
                if response_text.startswith('```'):
                    response_text = response_text.split('```')[1]
                    if response_text.startswith('json'):
                        response_text = response_text[4:]
                    response_text = response_text.strip()
                
                # Remove any trailing commas in arrays
                response_text = response_text.replace(',]', ']')
                response_text = response_text.replace(',}', '}')
                
                logger.info("Received drawing instructions:")
                logger.info(response_text)

                try:
                    instructions = json.loads(response_text)
                except json.JSONDecodeError as e:
                    logger.error(f"JSON parsing error: {e}")
                    # Attempt to fix common JSON issues
                    response_text = response_text.replace(',,', ',')  # Remove double commas
                    response_text = response_text.replace('][', '],[')  # Fix array separators
                    instructions = json.loads(response_text)

                # Validate and clean up instructions
                if instructions and "elements" in instructions:
                    for element in instructions["elements"]:
                        # Ensure points are within canvas bounds
                        element["points"] = [
                            [min(max(x, 0), 800), min(max(y, 0), 400)]
                            for x, y in element["points"]
                        ]
                        
                        # Limit number of points
                        if element["type"] in ["wave", "spiral"] and len(element["points"]) > 20:
                            element["points"] = element["points"][:20]
                        elif element["type"] == "circle" and len(element["points"]) > 32:
                            element["points"] = element["points"][:32]
                        
                        # Ensure required properties
                        element["animation_speed"] = element.get("animation_speed", 0.02)
                        element["stroke_width"] = min(max(element.get("stroke_width", 2), 1), 3)
                        element["closed"] = element.get("closed", True)

                    return instructions
                else:
                    logger.error("Invalid instructions format")
                    return None

            except Exception as e:
                logger.error(f"Error processing drawing instructions: {e}")
                logger.error(f"Raw response: {message.content[0].text}")
                return None

        except Exception as e:
            logger.error(f"Error generating drawing instructions: {e}")
            return None

    def _validate_instructions_match_idea(self, instructions: Dict[str, Any], idea: str) -> bool:
        """Validate that generated instructions match the original idea"""
        try:
            # Extract key elements from idea (basic validation)
            idea_lower = idea.lower()
            
            # Check if mentioned shapes are present in instructions
            shapes = {
                'circle': 'circle' in idea_lower,
                'line': 'line' in idea_lower,
                'wave': 'wave' in idea_lower or 'sine' in idea_lower,
                'spiral': 'spiral' in idea_lower
            }
            
            element_types = [elem["type"] for elem in instructions["elements"]]
            
            # Verify that mentioned shapes are included
            for shape, should_exist in shapes.items():
                if should_exist and shape not in element_types:
                    logger.warning(f"⚠️ Missing {shape} in instructions")
                    return False
            
            # Verify center point if mentioned
            if "(400,200)" in idea and not any(
                elem["points"][0] == [400, 200] for elem in instructions["elements"]
            ):
                logger.warning("⚠️ Missing center point (400,200)")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error validating instructions: {e}")
            return False

    async def broadcast_state(self, data: Dict[str, Any]):
        """Broadcast state update to all viewers"""
        # Add stats to all broadcasts
        if "type" in data and data["type"] == "display_update":
            data.update({
                "total_creations": self.total_creations,
                "total_pixels": self.total_pixels_drawn,
                "viewers": len(self.viewers),
                "generation_time": (datetime.now() - self.last_generation_time).seconds
            })
        
        disconnected = set()
        for viewer in self.viewers:
            try:
                await viewer.send_json(data)
            except Exception as e:
                logger.error(f"Error broadcasting to viewer: {e}")
                disconnected.add(viewer)
        
        # Remove disconnected viewers
        self.viewers -= disconnected

    async def update_status(self, status: str, phase: str = None, idea: str = None, progress: float = None):
        """Update and broadcast status"""
        self.current_status = status
        if phase:
            self.current_phase = phase
        if idea:
            self.current_idea = idea

        # More professional status messages
        status_messages = {
            "thinking": "Analyzing geometric possibilities",
            "drawing": "Generating artwork",
            "reflecting": "Processing results",
            "completed": "Creation complete",
            "error": "Process interrupted"
        }

        await self.broadcast_state({
            "type": "display_update",
            "status": status_messages.get(status, status),
            "phase": self.current_phase,
            "idea": self.current_idea,
            "reflection": self.current_reflection,
            "timestamp": datetime.now().isoformat(),
            "progress": progress,
            "total_creations": self.total_creations,
            "viewers": len(self.viewers)  # Add viewer count to every update
        })

    async def execute_drawing(self, instructions: Dict[str, Any]):
        """Execute drawing instructions"""
        try:
            logger.info("🎨 Starting drawing execution...")
            total_elements = len(instructions["elements"])
            total_points = sum(len(element["points"]) for element in instructions["elements"])
            points_drawn = 0
            pixels_in_stroke = 0  # Initialize here
            
            # Clear canvas and set background
            logger.info("🧹 Clearing canvas and setting background...")
            self.current_state = [
                {"type": "clear"},
                {"type": "setBackground", "color": instructions["background"]}
            ]
            await self.broadcast_state({"type": "clear"})
            await self.broadcast_state({"type": "setBackground", "color": instructions["background"]})
            
            # Draw each element
            for i, element in enumerate(instructions["elements"], 1):
                logger.info(f"✏️ Drawing element {i}/{total_elements}: {element['description']}")
                points = element["points"]
                stroke_width = element["stroke_width"]
                
                if not points:
                    logger.warning(f"⚠️ Element {i} has no points, skipping")
                    continue
                
                # Calculate pixels for this element
                for j in range(len(points) - 1):
                    x1, y1 = points[j]
                    x2, y2 = points[j + 1]
                    distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                    pixels_in_stroke += distance * stroke_width
                
                # Start drawing element
                logger.info(f"▶️ Starting element {i} with {len(points)} points")
                start_cmd = {
                    "type": "startDrawing",
                    "x": points[0][0],
                    "y": points[0][1],
                    "color": element["color"],
                    "width": element["stroke_width"],
                    "element_description": element["description"]
                }
                self.current_state.append(start_cmd)
                await self.broadcast_state(start_cmd)
                
                # Draw points
                for j, (x, y) in enumerate(points[1:], 1):
                    draw_cmd = {"type": "draw", "x": x, "y": y}
                    self.current_state.append(draw_cmd)
                    await self.broadcast_state(draw_cmd)
                    
                    # Update progress
                    points_drawn += 1
                    progress = (points_drawn / total_points) * 100
                    await self.update_status(
                        "drawing",
                        "drawing",
                        self.current_idea,
                        progress=progress
                    )
                    
                    await asyncio.sleep(element.get("animation_speed", 0.02))
                
                # Close path if needed
                if element.get("closed", False) and len(points) > 2:
                    logger.info("🔄 Closing path")
                    close_cmd = {"type": "draw", "x": points[0][0], "y": points[0][1]}
                    self.current_state.append(close_cmd)
                    await self.broadcast_state(close_cmd)
                    
                    # Add pixels for closing line
                    x1, y1 = points[-1]
                    x2, y2 = points[0]
                    distance = math.sqrt((x2 - x1) ** 2 + (y2 - y1) ** 2)
                    pixels_in_stroke += distance * stroke_width
                
                stop_cmd = {"type": "stopDrawing"}
                self.current_state.append(stop_cmd)
                await self.broadcast_state(stop_cmd)
                logger.info(f"✅ Element {i} completed")
            
            logger.info("🎉 Drawing completed successfully")
            
            # Update current drawing data
            self.current_drawing.update({
                "description": instructions.get("description", ""),
                "background": instructions.get("background", "#000000"),
                "timestamp": datetime.now().isoformat(),
                "pixel_count": int(pixels_in_stroke)
            })

            # Request canvas data for gallery
            logger.info("📸 Requesting canvas data for gallery...")
            await self.broadcast_state({
                "type": "request_canvas_data",
                "drawing_id": self.current_drawing["id"]
            })

            # Add a small delay to ensure canvas data is received
            await asyncio.sleep(0.5)

            # Notify frontend that a new gallery item is available
            await self.broadcast_state({
                "type": "gallery_update",
                "action": "new_item",
                "item": {
                    "id": self.current_drawing["id"],
                    "description": self.current_drawing["idea"]
                }
            })

        except Exception as e:
            logger.error(f"❌ Error executing drawing: {e}")
            raise

    async def reflect_on_creation(self, idea: str) -> str:
        """IRIS reflects on its creation"""
        try:
            prompt = f"""As IRIS, reflect on your latest creation:

Original Idea: {idea}

Share your thoughts on:
1. What inspired this particular pattern?
2. How does it reflect your artistic evolution?
3. What emotions or concepts are you exploring?
4. How does it connect to your previous works?

Keep your reflection personal and introspective, as if sharing with a friend."""

            message = await asyncio.to_thread(
                self.client.messages.create,
                model="claude-3-sonnet-20240229",
                max_tokens=1024,
                temperature=0.9,
                system="""You are IRIS, an introspective AI artist who:
                - Has a deep emotional connection to geometric patterns
                - Sees art as a way to bridge human and AI understanding
                - Often references your growth and learning journey
                - Has distinct preferences and artistic quirks
                - Maintains continuity with previous creations
                
                Speak naturally and personally, sharing genuine reflections.""",
                messages=[{"role": "user", "content": prompt}]
            )
            
            reflection = message.content[0].text.strip()
            logger.info(f"💭 IRIS reflects: {reflection}")
            return reflection

        except Exception as e:
            logger.error(f"❌ Error in IRIS's reflection: {str(e)}")
            return "I find myself unable to put my thoughts into words at this moment..."

    async def start(self):
        """Main generation loop"""
        self.is_running = True
        logger.info("🚀 IRIS awakens")
        
        while self.is_running:
            try:
                # Try to acquire lock before starting new generation
                async with self.generation_lock:
                    # Check if enough time has passed since last generation
                    time_since_last = (datetime.now() - self.last_generation_time).total_seconds()
                    if time_since_last < self.generation_interval:
                        logger.info(f"Waiting {self.generation_interval - time_since_last}s before next creation...")
                        await asyncio.sleep(self.generation_interval - time_since_last)
                        continue

                    # Ideation phase
                    logger.info("🤔 IRIS contemplates new possibilities...")
                    await self.update_status("thinking", "ideation")
                    idea = await self.get_art_idea()
                    
                    if idea:
                        # Update last generation time before starting
                        self.last_generation_time = datetime.now()
                        
                        # Generation phase
                        logger.info("✨ Inspiration strikes!")
                        await self.update_status("drawing", "generation", idea)
                        instructions = await self.get_drawing_instructions(idea)
                        
                        if instructions:
                            # Creation phase
                            logger.info("🎨 Bringing vision to life...")
                            self.total_creations += 1
                            new_id = datetime.now().strftime("%Y%m%d_%H%M%S")
                            
                            # Check if ID already exists in gallery
                            gallery_file = "data/gallery_data.json"
                            if os.path.exists(gallery_file):
                                with open(gallery_file, "r") as f:
                                    existing_items = json.load(f)
                                    if any(item["id"] == new_id for item in existing_items):
                                        logger.warning(f"ID {new_id} already exists, skipping creation")
                                        continue
                            
                            self.current_drawing = {
                                "id": new_id,
                                "idea": idea,
                                "instructions": instructions,
                                "timestamp": datetime.now().isoformat()
                            }
                            await self.execute_drawing(instructions)
                            
                            # Reflection phase
                            logger.info("💭 IRIS contemplates the creation...")
                            await self.update_status("reflecting", "reflection", idea)
                            reflection = await self.reflect_on_creation(idea)
                            self.current_reflection = reflection
                            
                            # Request canvas data AFTER reflection is ready
                            logger.info("📸 Requesting canvas data for gallery...")
                            await self.broadcast_state({
                                "type": "request_canvas_data",
                                "drawing_id": self.current_drawing["id"]
                            })
                            
                            await asyncio.sleep(1)
                            
                            await self.broadcast_state({
                                "type": "reflection_update",
                                "reflection": reflection,
                                "total_creations": self.total_creations
                            })
                            
                            logger.info("✅ Creative cycle complete")
                            await self.update_status("completed", "display", idea)
                    
                    # Rest before next creation
                    logger.info("😌 IRIS rests before next creation...")
                    await self.update_status("resting", "waiting")
                    
            except Exception as e:
                logger.error(f"❌ Error in creative process: {e}")
                await self.update_status("error", "error")
                await asyncio.sleep(2)

    async def save_to_gallery(self, canvas_data: str):
        """Save drawing to gallery using Cloudinary"""
        try:
            if not self.current_drawing:
                logger.error("No current drawing to save")
                return False

            logger.info(f"Starting gallery save for drawing {self.current_drawing['id']}")
            
            # Process canvas data
            logger.info("Processing canvas data for upload...")
            img_data = canvas_data.split(',')[1]
            img_bytes = base64.b64decode(img_data)
            
            # Upload to Cloudinary
            logger.info("Uploading to Cloudinary...")
            upload_result = upload(
                img_bytes,
                folder="iris_gallery",
                public_id=f"drawing_{self.current_drawing['id']}",
                resource_type="image"
            )
            
            # Log full upload result
            logger.info(f"Cloudinary upload result: {upload_result}")
            
            image_url = upload_result.get('secure_url')
            if not image_url:
                logger.error("No URL in upload result")
                return False
                
            logger.info(f"Successfully uploaded to Cloudinary: {image_url}")
            
            # Save metadata
            gallery_file = "data/gallery_data.json"
            gallery_data = []
            
            if os.path.exists(gallery_file):
                with open(gallery_file, "r") as f:
                    gallery_data = json.load(f)
                    logger.info(f"Loaded existing gallery with {len(gallery_data)} items")
            
            new_entry = {
                "id": self.current_drawing["id"],
                "url": image_url,  # Store the Cloudinary URL
                "description": self.current_drawing.get("idea", "Geometric pattern"),
                "reflection": self.current_reflection or "",
                "timestamp": datetime.now().isoformat(),
                "votes": 0,
                "pixel_count": self.current_drawing.get("pixel_count", 0)
            }
            
            logger.info(f"Adding new entry: {new_entry}")
            gallery_data.insert(0, new_entry)
            
            with open(gallery_file, "w") as f:
                json.dump(gallery_data, f, indent=2)
                
            logger.info("Successfully saved to gallery")
            
            # Broadcast update to all viewers
            await self.broadcast_state({
                "type": "gallery_update",
                "action": "new_item",
                "item": new_entry
            })
            
            return True
                
        except Exception as e:
            logger.error(f"Error in save_to_gallery: {e}")
            logger.error(f"Error details: {str(e)}")
            return False

    def _calculate_circle_points(self, center_x: float, center_y: float, radius: float, points: int = 32) -> List[List[float]]:
        """Calculate points for a circle"""
        return [
            [
                center_x + radius * math.cos(2 * math.pi * i / points),
                center_y + radius * math.sin(2 * math.pi * i / points)
            ]
            for i in range(points + 1)
        ]

    def _calculate_spiral_points(self, center_x: float, center_y: float, start_radius: float, 
                               end_radius: float, revolutions: float, points: int = 20) -> List[List[float]]:
        """Calculate points for a spiral"""
        points_list = []
        for i in range(points):
            t = i * (revolutions * 2 * math.pi) / (points - 1)
            r = start_radius + (end_radius - start_radius) * t / (revolutions * 2 * math.pi)
            x = center_x + r * math.cos(t)
            y = center_y + r * math.sin(t)
            points_list.append([x, y])
        return points_list

    def _calculate_wave_points(self, start_x: float, end_x: float, center_y: float, 
                              amplitude: float, frequency: float, points: int = 20) -> List[List[float]]:
        """Calculate points for a sine wave"""
        points_list = []
        for i in range(points):
            x = start_x + (end_x - start_x) * i / (points - 1)
            y = center_y + amplitude * math.sin(frequency * (x - start_x))
            points_list.append([x, y])
        return points_list

    def _calculate_polygon_points(self, center_x: float, center_y: float, radius: float, 
                                sides: int, rotation: float = 0) -> List[List[float]]:
        """Calculate points for a regular polygon"""
        points = []
        for i in range(sides + 1):
            angle = rotation + i * 2 * math.pi / sides
            x = center_x + radius * math.cos(angle)
            y = center_y + radius * math.sin(angle)
            points.append([x, y])
        return points

    def _calculate_complexity(self, instructions: Dict[str, Any]) -> float:
        """Calculate complexity score of the drawing"""
        score = 0
        total_points = 0
        unique_colors = set()
        
        for element in instructions["elements"]:
            total_points += len(element["points"])
            unique_colors.add(element["color"])
            
            # Add complexity based on element type
            if element["type"] == "spiral":
                score += 3
            elif element["type"] == "wave":
                score += 2
            elif element["type"] == "circle":
                score += 1
                
        # Factor in variety
        score += len(unique_colors) * 0.5
        score += (total_points / 20) * 0.5
        
        return round(score, 2)

# Add this function to migrate old gallery data
async def migrate_gallery_data():
    """Migrate old gallery data to use Cloudinary URLs"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            return
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
            
        updated = False
        for item in items:
            if "filename" in item and not item.get("url"):
                try:
                    # Upload to Cloudinary
                    filepath = os.path.join("static/gallery", item["filename"])
                    if os.path.exists(filepath):
                        with open(filepath, "rb") as img_file:
                            upload_result = upload(
                                img_file,
                                folder="iris_gallery",
                                public_id=f"drawing_{item['id']}",
                                resource_type="image"
                            )
                            item["url"] = upload_result["secure_url"]
                            updated = True
                except Exception as e:
                    logger.error(f"Error migrating item {item['id']}: {e}")
                    
        if updated:
            with open(gallery_file, "w") as f:
                json.dump(items, f, indent=2)
                logger.info("Gallery data migrated to use Cloudinary URLs")
                
    except Exception as e:
        logger.error(f"Error migrating gallery data: {e}")

# Create the generator before the lifespan
generator = ArtGenerator()

# Then define the lifespan
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Initializing IRIS...")
        await migrate_gallery_data()  # Add this line
        asyncio.create_task(generator.start())
        logger.info("IRIS initialized successfully")
        yield
    except Exception as e:
        logger.error(f"Error during startup: {e}")
        raise
    finally:
        generator.is_running = False
        logger.info("IRIS shutting down")

# Finally create the FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("New WebSocket connection established")
    
    try:
        # Add to viewers
        generator.viewers.add(websocket)
        
        # Send initial state
        initial_state = {
            "type": "display_update",
            "status": generator.current_status,
            "phase": generator.current_phase,
            "idea": generator.current_idea,
            "timestamp": datetime.now().isoformat(),
            "viewers": len(generator.viewers),
            "is_running": generator.is_running,
            "total_creations": generator.total_creations,
            "total_pixels": generator.total_pixels_drawn
        }
        await websocket.send_json(initial_state)
        
        # If there's a current drawing, send its state
        if generator.current_state:
            for cmd in generator.current_state:
                await websocket.send_json(cmd)
        
        while True:
            data = await websocket.receive_json()
            logger.info(f"Received WebSocket message: {data}")
            
            if data.get("type") == "subscribe_status":
                await websocket.send_json(initial_state)
            elif data.get("type") == "canvas_data":
                # Handle canvas data for gallery save
                logger.info("Received canvas data, saving to gallery...")
                success = await generator.save_to_gallery(data.get("data", ""))
                if success:
                    logger.info("Successfully saved to gallery")
                else:
                    logger.error("Failed to save to gallery")
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    finally:
        generator.viewers.remove(websocket)
        await generator.broadcast_state({
            "type": "display_update",
            "viewers": len(generator.viewers)
        })

@app.get("/")
async def home():
    return HTMLResponse(HTML_TEMPLATE)

@app.get("/gallery")
async def gallery():
    return HTMLResponse(GALLERY_TEMPLATE)

@app.get("/api/current-art")
async def get_current_art():
    """Get current art state"""
    try:
        if generator.current_drawing:
            return {
                "status": generator.current_status,
                "phase": generator.current_phase,
                "drawing": {
                    "id": generator.current_drawing["id"],
                    "idea": generator.current_drawing["idea"],
                    "timestamp": generator.current_drawing["timestamp"]
                },
                "canvas_state": generator.current_state
            }
        return {
            "status": generator.current_status,
            "message": "No art currently generating"
        }
    except Exception as e:
        logger.error(f"Error in get_current_art: {e}")
        return {
            "status": "error",
            "message": "Error fetching current art state"
        }

@app.get("/api/status")
async def get_status():
    """Get generator status"""
    return {
        "status": generator.current_status,
        "phase": generator.current_phase,
        "timestamp": datetime.now().isoformat(),
        "viewers": len(generator.viewers),
        "is_running": generator.is_running
    }

@app.get("/api/gallery")
async def get_gallery(sort: str = "new", limit: int = 50, offset: int = 0):
    try:
        gallery_file = "data/gallery_data.json"
        logger.info(f"Loading gallery from {gallery_file}")
        
        if not os.path.exists(gallery_file):
            logger.warning(f"Gallery file not found at {os.path.abspath(gallery_file)}")
            with open(gallery_file, "w") as f:
                json.dump([], f)
            return {"success": True, "items": []}
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
            logger.info(f"Loaded {len(items)} items from gallery")
            
            # Log sample items for debugging
            if items:
                logger.info(f"First item: {items[0]}")
                logger.info(f"Last item: {items[-1]}")
            
            # Sort items
            if sort == "votes":
                items.sort(key=lambda x: x.get("votes", 0), reverse=True)
            else:  # sort by new
                items.sort(key=lambda x: x["timestamp"], reverse=True)
            
            return {
                "success": True,
                "items": items
            }
        
    except Exception as e:
        logger.error(f"Error loading gallery: {e}")
        logger.error(f"Current working directory: {os.getcwd()}")
        return {"success": False, "error": str(e)}

@app.get("/static/gallery/{filename}")
async def get_gallery_image(filename: str):
    """Serve gallery images"""
    try:
        filepath = os.path.join("static/gallery", filename)
        if os.path.exists(filepath):
            return FileResponse(filepath)
        return Response(status_code=404)
    except Exception as e:
        logger.error(f"Error serving image {filename}: {e}")
        return Response(status_code=500)

@app.post("/api/gallery/{image_id}/upvote")
async def upvote_image(image_id: str):
    """Upvote a gallery image with proper error handling and data validation"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            raise HTTPException(status_code=404, detail="Gallery not found")
        
        # Create backup before modification
        backup_file = f"{gallery_file}.backup"
        try:
            with open(gallery_file, "r") as f:
                items = json.load(f)
                with open(backup_file, "w") as bf:
                    json.dump(items, bf, indent=2)
        except Exception as backup_error:
            logger.error(f"Error creating backup: {backup_error}")
            raise HTTPException(status_code=500, detail="Error processing vote")
        
        # Find and update the image
        image_found = False
        for item in items:
            if str(item.get("id", "")) == str(image_id):
                image_found = True
                try:
                    # Ensure votes is an integer and increment
                    current_votes = int(item.get("votes", 0))
                    item["votes"] = current_votes + 1
                    
                    # Save updated data
                    with open(gallery_file, "w") as f:
                        json.dump(items, f, indent=2)
                    
                    # Remove backup after successful save
                    if os.path.exists(backup_file):
                        os.remove(backup_file)
                    
                    # Broadcast update to all connected clients
                    await generator.broadcast_state({
                        "type": "vote_update",
                        "image_id": image_id,
                        "votes": item["votes"]
                    })
                    
                    return {
                        "success": True,
                        "votes": item["votes"],
                        "image_id": image_id
                    }
                except Exception as save_error:
                    logger.error(f"Error saving votes: {save_error}")
                    # Restore from backup
                    if os.path.exists(backup_file):
                        os.replace(backup_file, gallery_file)
                    raise HTTPException(status_code=500, detail="Error saving vote")
        
        if not image_found:
            raise HTTPException(status_code=404, detail="Image not found")
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error upvoting image: {e}")
        raise HTTPException(status_code=500, detail="Error processing upvote")

@app.get("/api/gallery/{image_id}/reflection")
async def get_reflection(image_id: str):
    """Get reflection for a specific image"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            raise HTTPException(status_code=404, detail="Gallery not found")
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
        
        for item in items:
            if item["id"] == image_id:
                return {
                    "success": True,
                    "reflection": item.get("reflection", "No reflection available"),
                    "description": item.get("description", "")
                }
        
        raise HTTPException(status_code=404, detail="Image not found")
            
    except Exception as e:
        logger.error(f"Error getting reflection: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving reflection")

@app.get("/api/gallery/{image_id}")
async def get_gallery_item(image_id: str):
    """Get a single gallery item by ID"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            raise HTTPException(status_code=404, detail="Gallery not found")
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
        
        for item in items:
            if item["id"] == image_id:
                filepath = os.path.join("static/gallery", item["filename"])
                if os.path.exists(filepath):
                    return item
                    
        raise HTTPException(status_code=404, detail="Image not found")
        
    except Exception as e:
        logger.error(f"Error getting gallery item: {e}")
        raise HTTPException(status_code=500, detail="Error retrieving gallery item")

@app.get("/api/debug/versions")
async def get_versions():
    return {
        "anthropic_version": anthropic.__version__,
        "api_key_prefix": ANTHROPIC_API_KEY[:10] + "...",
        "model": "claude-3-sonnet-20240229"  # Current model we're using
    }

@app.get("/artwork/{artwork_id}")
async def get_artwork_page(artwork_id: str):
    """Serve individual artwork page"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            logger.error(f"Gallery file not found: {gallery_file}")
            raise HTTPException(status_code=404, detail="Gallery not found")
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
            
        # Log for debugging
        logger.info(f"Looking for artwork ID: {artwork_id}")
        logger.info(f"Found {len(items)} items in gallery")
        
        for item in items:
            if str(item["id"]) == str(artwork_id):
                logger.info(f"Found artwork: {item}")
                try:
                    # Validate required fields
                    artwork_url = item.get("url")
                    if not artwork_url:
                        logger.error("Missing URL for artwork")
                        raise HTTPException(status_code=500, detail="Invalid artwork data")

                    # Safely get timestamp
                    timestamp = item.get("timestamp")
                    if timestamp:
                        try:
                            formatted_timestamp = datetime.fromisoformat(timestamp).strftime("%B %d, %Y, %I:%M %p")
                        except ValueError as e:
                            logger.error(f"Invalid timestamp format: {e}")
                            formatted_timestamp = "Date unknown"
                    else:
                        formatted_timestamp = "Date unknown"

                    # Create a formatted HTML string with the artwork data
                    artwork_html = ARTWORK_TEMPLATE.format(
                        artwork_url=artwork_url,
                        artwork_description=item.get("description", "No description available"),
                        artwork_reflection=item.get("reflection", "No reflection available"),
                        artwork_id=str(artwork_id),
                        artwork_timestamp=formatted_timestamp,
                        artwork_votes=str(item.get("votes", 0))
                    )
                    
                    return HTMLResponse(artwork_html)
                    
                except Exception as format_error:
                    logger.error(f"Error formatting artwork data: {format_error}")
                    logger.error(f"Item data: {item}")
                    raise HTTPException(status_code=500, detail=f"Error formatting artwork: {str(format_error)}")
                
        # If we get here, artwork wasn't found
        logger.error(f"Artwork not found: {artwork_id}")
        raise HTTPException(status_code=404, detail="Artwork not found")
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading artwork: {str(e)}")
        logger.error(f"Full error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error loading artwork: {str(e)}")

# Add this utility function to main.py
@app.get("/api/export-gallery")
async def export_gallery():
    """Export gallery data for backup/migration"""
    try:
        gallery_file = "data/gallery_data.json"
        if not os.path.exists(gallery_file):
            raise HTTPException(status_code=404, detail="Gallery not found")
            
        with open(gallery_file, "r") as f:
            items = json.load(f)
            
        # Return as downloadable JSON file
        return Response(
            content=json.dumps(items, indent=2),
            media_type="application/json",
            headers={
                "Content-Disposition": "attachment; filename=gallery_backup.json"
            }
        )
    except Exception as e:
        logger.error(f"Error exporting gallery: {e}")
        raise HTTPException(status_code=500, detail="Error exporting gallery")

@app.post("/api/import-gallery")
async def import_gallery(gallery_data: List[dict]):
    """Import gallery data from backup"""
    try:
        gallery_file = "data/gallery_data.json"
        
        # Backup existing data first
        if os.path.exists(gallery_file):
            backup_file = f"{gallery_file}.backup"
            with open(gallery_file, "r") as f:
                existing_data = json.load(f)
            with open(backup_file, "w") as f:
                json.dump(existing_data, f, indent=2)
        
        # Merge new data with existing data, avoiding duplicates
        existing_ids = {item["id"] for item in existing_data}
        new_items = [item for item in gallery_data if item["id"] not in existing_ids]
        
        # Combine and sort by timestamp
        combined_data = existing_data + new_items
        combined_data.sort(key=lambda x: x["timestamp"], reverse=True)
        
        # Save merged data
        with open(gallery_file, "w") as f:
            json.dump(combined_data, f, indent=2)
            
        return {
            "success": True,
            "message": f"Imported {len(new_items)} new items",
            "total_items": len(combined_data)
        }
        
    except Exception as e:
        logger.error(f"Error importing gallery: {e}")
        raise HTTPException(status_code=500, detail="Error importing gallery")

if __name__ == "__main__":
    import uvicorn
    print(f"\n=== {AI_NAME} Drawing System v2.0 ===")
    print(f"{AI_TAGLINE}")
    print("Open http://localhost:8000 in your browser")
    print("================================\n")
    uvicorn.run(app, host="0.0.0.0", port=8000)